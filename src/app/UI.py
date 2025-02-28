"""Main UI module, brings everything together."""
import tkinter as tk
from tkinter import ttk
from typing import List, Dict, Tuple, Optional, Set, Iterator, Callable, Any, Union
import itertools
import operator
import random
import functools
import math

import srctools.logger
import trio

import loadScreen
from app import TK_ROOT, background_run, localisation
from app.itemPropWin import PROP_TYPES
from BEE2_config import ConfigFile, GEN_OPTS
from loadScreen import main_loader as loader
import packages
from packages.item import ItemVariant, InheritKind
import utils
import consts
from config.gen_opts import GenOptions, AfterExport
from config.last_sel import LastSelected
from config.windows import WindowState
import config
from transtoken import TransToken
from app import (
    img,
    itemconfig,
    sound as snd,
    tk_tools,
    SubPane,
    voiceEditor,
    contextWin,
    gameMan,
    packageMan,
    StyleVarPane,
    CompilerPane,
    item_search,
    corridor_selector,
    optionWindow,
    backup as backup_win,
    tooltip,
    signage_ui,
    paletteUI,
    music_conf,
)
from app.selector_win import SelectorWin, Item as selWinItem, AttrDef as SelAttr
from app.menu_bar import MenuBar


LOGGER = srctools.logger.get_logger(__name__)

# Holds the TK Toplevels, frames, widgets and menus
windows: Dict[str, Any] = {}  # Toplevel | SubPane
frames: Dict[str, Union[tk.Frame, ttk.Frame]] = {}
UI: Dict[str, Any] = {}  # Various widgets.

# These panes.
skybox_win: 'SelectorWin[[]]'
voice_win: 'SelectorWin[[]]'
style_win: 'SelectorWin[[]]'
elev_win: 'SelectorWin[[]]'

# Items chosen for the palette.
pal_picked: List['PalItem'] = []
# Array of the "all items" icons
pal_items: List['PalItem'] = []
# Labels used for the empty palette positions
pal_picked_fake: List[ttk.Label] = []
# Labels for empty picker positions
pal_items_fake: List[ttk.Label] = []
# The current filtering state.
cur_filter: Optional[Set[Tuple[str, int]]] = None

ItemsBG = "#CDD0CE"  # Colour of the main background to match the menu image

# Icon shown while items are being moved elsewhere.
ICO_MOVING = img.Handle.builtin('BEE2/item_moving', 64, 64)
ICO_GEAR = img.Handle.sprite('icons/gear', 10, 10)
ICO_GEAR_DIS = img.Handle.sprite('icons/gear_disabled', 10, 10)
IMG_BLANK = img.Handle.color(img.PETI_ITEM_BG, 64, 64)

selected_style = "BEE2_CLEAN"

# Maps item IDs to our wrapper for the object.
item_list: Dict[str, 'Item'] = {}

item_opts = ConfigFile('item_configs.cfg')
# A config file which remembers changed property options, chosen
# versions, etc

# Piles of global widgets, should be made local...
frmScroll: ttk.Frame  # Frame holding the item list.
pal_canvas: tk.Canvas  # Canvas for the item list to scroll.


TRANS_EXPORTED = TransToken.ui('Selected Items and Style successfully exported!')
TRANS_EXPORTED_NO_VPK = TransToken.ui(
    '{exported}\n\nWarning: VPK files were not exported, quit Portal 2 and '
    'Hammer to ensure editor wall previews are changed.'
).format(exported=TRANS_EXPORTED)
TRANS_EXPORTED_TITLE = TransToken.ui('BEE2 - Export Complete')
TRANS_MAIN_TITLE = TransToken.ui('BEEMOD {version} - {game}')
TRANS_ERROR = TransToken.untranslated('???')


class Item:
    """Represents an item that can appear on the list."""
    __slots__ = [
        'ver_list',
        'selected_ver',
        'item',
        'def_data',
        'data',
        'inherit_kind',
        'visual_subtypes',
        'authors',
        'id',
        'pak_id',
        'pak_name',
        'names',
        ]
    data: ItemVariant
    inherit_kind: InheritKind

    def __init__(self, item: packages.Item) -> None:
        self.ver_list = sorted(item.versions.keys())

        self.selected_ver = item_opts.get_val(
            item.id,
            'sel_version',
            item.def_ver.id,
        )
        # If the last-selected value doesn't exist, fallback to the default.
        if self.selected_ver not in item.versions:
            LOGGER.warning('Version ID {} is not valid for item {}', self.selected_ver, item.id)
            self.selected_ver = item.def_ver.id

        self.item = item
        self.def_data = item.def_ver.def_style
        # The indexes of subtypes that are actually visible.
        self.visual_subtypes = [
            ind
            for ind, sub in enumerate(self.def_data.editor.subtypes)
            if sub.pal_name or sub.pal_icon
        ]

        self.authors = self.def_data.authors
        self.id = item.id
        self.pak_id = item.pak_id
        self.pak_name = item.pak_name

        self.load_data()

    def load_data(self) -> None:
        """Reload data from the item."""
        vers = self.item.versions[self.selected_ver]
        self.data = vers.styles.get(selected_style, self.def_data)
        self.inherit_kind = vers.inherit_kind.get(selected_style, InheritKind.UNSTYLED)

    def get_tags(self, subtype: int) -> Iterator[str]:
        """Return all the search keywords for this item/subtype."""
        yield self.pak_name
        yield from self.data.tags
        yield from self.data.authors
        try:
            name = self.data.editor.subtypes[subtype].name
        except IndexError:
            LOGGER.warning(
                'No subtype number {} for {} in {} style!',
                subtype, self.id, selected_style,
            )
        else:  # Include both the original and translated versions.
            if not name.is_game:
                yield name.token
            yield str(name)

    def get_icon(self, subKey, allow_single=False, single_num=1) -> img.Handle:
        """Get an icon for the given subkey.

        If allow_single is true, the grouping icon can be returned
        instead if only one item is on the palette.
        Drag-icons have different rules for what counts as 'single', so
        they use the single_num parameter to control the output.
        """
        icon = self._get_raw_icon(subKey, allow_single, single_num)
        if self.item.unstyled or not config.APP.get_cur_conf(GenOptions).visualise_inheritance:
            return icon
        if self.inherit_kind is not InheritKind.DEFINED:
            icon = icon.overlay_text(self.inherit_kind.value.title(), 12)
        return icon

    def _get_raw_icon(self, subKey, allow_single: bool, single_num: int) -> img.Handle:
        """Get the raw icon, which may be overlaid if required."""
        icons = self.data.icons
        num_picked = sum(
            item.id == self.id
            for item in pal_picked
        )
        if allow_single and self.data.can_group() and num_picked <= single_num:
            # If only 1 copy of this item is on the palette, use the
            # special icon
            try:
                return icons['all']
            except KeyError:
                return img.Handle.file(utils.PackagePath(
                    self.pak_id, str(self.data.all_icon)
                ), 64, 64)

        try:
            return icons[str(subKey)]
        except KeyError:
            # Read from editoritems.
            pass
        try:
            subtype = self.data.editor.subtypes[subKey]
        except IndexError:
            LOGGER.warning(
                'No subtype number {} for {} in {} style!',
                subKey, self.id, selected_style,
            )
            return img.Handle.error(64, 64)
        if subtype.pal_icon is None:
            LOGGER.warning(
                'No palette icon for {} subtype {} in {} style!',
                self.id, subKey, selected_style,
            )
            return img.Handle.error(64, 64)

        return img.Handle.file(utils.PackagePath(
            self.data.pak_id, str(subtype.pal_icon)
        ), 64, 64)

    def properties(self) -> Iterator[str]:
        """Iterate through all properties for this item."""
        for prop_name, prop in self.data.editor.properties.items():
            if prop.allow_user_default:
                yield prop_name

    def get_properties(self) -> Dict[str, Any]:
        """Return a dictionary of properties and the current value for them.

        """
        result = {}
        for prop_name, prop in self.data.editor.properties.items():
            if not prop.allow_user_default:
                continue

            # PROP_TYPES is a dict holding all the modifiable properties.
            if prop_name in PROP_TYPES:
                result[prop_name] = item_opts.get_val(
                    self.id,
                    'PROP_' + prop_name,
                    prop.export(),
                )
            else:
                LOGGER.warning(
                    'Unknown property "{}" in {}',
                    prop_name,
                    self.id,
                )
        return result

    def set_properties(self, props: Dict[str, Any]) -> None:
        """Apply the properties to the item."""
        for prop, value in props.items():
            item_opts[self.id]['PROP_' + prop] = str(value)

    def refresh_subitems(self) -> None:
        """Call load_data() on all our subitems, so they reload icons and names."""
        for item in pal_picked:
            if item.id == self.id:
                item.load_data()
        flow_preview()
        for item in pal_items:
            if item.id == self.id:
                item.load_data()
        flow_picker()

    def change_version(self, version: str) -> None:
        """Set the version of this item."""
        item_opts[self.id]['sel_version'] = version
        self.selected_ver = version
        self.load_data()
        self.refresh_subitems()

    def get_version_names(self) -> Tuple[List[str], List[str]]:
        """Get a list of the names and corresponding IDs for the item."""
        # item folders are reused, so we can find duplicates.
        style_obj_ids = {
            id(self.item.versions[ver_id].styles[selected_style])
            for ver_id in self.ver_list
        }
        versions = self.ver_list
        if len(style_obj_ids) == 1:
            # All the variants are the same, so we effectively have one
            # variant. Disable the version display.
            versions = self.ver_list[:1]

        return versions, [
            self.item.versions[ver_id].name
            for ver_id in versions
        ]


class PalItem:
    """The icon and associated data for a single subitem."""
    def __init__(self, frame, item: Item, sub: int, is_pre: bool) -> None:
        """Create a label to show an item onscreen."""
        self.item = item
        self.subKey = sub
        self.id = item.id
        # Used to distinguish between picker and palette items
        self.is_pre = is_pre
        self.needs_unlock = item.item.needs_unlock

        # Location this item was present at previously when dragging it.
        self.pre_x = self.pre_y = -1

        self.label = lbl = tk.Label(frame)

        lbl.bind(tk_tools.EVENTS['LEFT'], functools.partial(drag_start, self))
        lbl.bind(tk_tools.EVENTS['LEFT_SHIFT'], functools.partial(drag_fast, self))
        lbl.bind("<Enter>", self.rollover)
        lbl.bind("<Leave>", self.rollout)

        self.info_btn = tk.Label(
            lbl,
            relief='ridge',
            width=12,
            height=12,
        )
        img.apply(self.info_btn, ICO_GEAR)

        click_func = contextWin.open_event(self)
        tk_tools.bind_rightclick(lbl, click_func)

        @tk_tools.bind_leftclick(self.info_btn)
        def info_button_click(e):
            click_func(e)
            # Cancel the event sequence, so it doesn't travel up to the main
            # window and hide the window again.
            return 'break'

        # Rightclick does the same as the icon.
        tk_tools.bind_rightclick(self.info_btn, click_func)

    @property
    def name(self) -> TransToken:
        """Get the current name for this subtype."""
        try:
            return self.item.data.editor.subtypes[self.subKey].name
        except IndexError:
            LOGGER.warning(
                'Item <{}> in <{}> style has mismatched subtype count!',
                self.id, selected_style,
            )
            return TRANS_ERROR

    def rollover(self, _: tk.Event) -> None:
        """Show the name of a subitem and info button when moused over."""
        set_disp_name(self)
        self.label.lift()
        self.label['relief'] = 'ridge'
        padding = 2 if utils.WIN else 0
        self.info_btn.place(
            x=self.label.winfo_width() - padding,
            y=self.label.winfo_height() - padding,
            anchor='se',
        )

    def rollout(self, _: tk.Event) -> None:
        """Reset the item name display and hide the info button when the mouse leaves."""
        clear_disp_name()
        self.label['relief'] = 'flat'
        self.info_btn.place_forget()

    def change_subtype(self, ind) -> None:
        """Change the subtype of this icon.

        This removes duplicates from the palette if needed.
        """
        for item in pal_picked[:]:
            if item.id == self.id and item.subKey == ind:
                item.kill()
        self.subKey = ind
        self.load_data()
        self.label.master.update()  # Update the frame
        flow_preview()

    def open_menu_at_sub(self, ind: int) -> None:
        """Make the contextWin open itself at the indicated subitem.

        """
        if self.is_pre:
            items_list = pal_picked[:]
        else:
            items_list = []
        # Open on the palette, but also open on the item picker if needed
        for item in itertools.chain(items_list, pal_items):
            if item.id == self.id and item.subKey == ind:
                contextWin.show_prop(item, warp_cursor=True)
                break

    def load_data(self) -> None:
        """Refresh our icon and name.

        Call whenever the style changes, so the icons update.
        """
        img.apply(self.label, self.item.get_icon(self.subKey, self.is_pre))

    def clear(self) -> bool:
        """Remove any items matching ourselves from the palette.

        This prevents adding two copies.
        """
        found = False
        for item in pal_picked[:]:
            # remove the item off of the palette if it's on there, this
            # lets you delete items and prevents having the same item twice.
            if self.id == item.id and self.subKey == item.subKey:
                item.kill()
                found = True
        return found

    def kill(self) -> None:
        """Hide and destroy this widget."""
        for i, item in enumerate(pal_picked):
            if item is self:
                del pal_picked[i]
                break
        self.label.place_forget()

    def on_pal(self) -> bool:
        """Determine if this item is on the palette."""
        for item in pal_picked:
            if self.id == item.id and self.subKey == item.subKey:
                return True
        return False

    def copy(self, frame):
        return PalItem(frame, self.item, self.subKey, self.is_pre)

    def __repr__(self) -> str:
        return f'<{self.id}:{self.subKey}>'


def quit_application() -> None:
    """Do a last-minute save of our config files, and quit the app."""
    import app
    LOGGER.info('Shutting down application.')
    # noinspection PyProtectedMember
    if app._APP_NURSERY is not None:
        # noinspection PyProtectedMember
        app._APP_NURSERY.cancel_scope.cancel()

    # If our window isn't actually visible, this is set to nonsense -
    # ignore those values.
    if TK_ROOT.winfo_viewable():
        config.APP.store_conf(WindowState(
            x=TK_ROOT.winfo_rootx(),
            y=TK_ROOT.winfo_rooty(),
        ), 'main_window')

    try:
        config.APP.write_file()
    except Exception:
        LOGGER.exception('Saving main conf:')
    try:
        GEN_OPTS.save_check()
    except Exception:
        LOGGER.exception('Saving GEN_OPTS:')

    item_opts.save_check()
    CompilerPane.COMPILE_CFG.save_check()
    try:
        gameMan.save()
    except Exception:
        pass
    # Clean this out.
    snd.clean_sample_folder()

    # Destroy the TK windows, finalise logging, then quit.
    loadScreen.shutdown()

gameMan.quit_application = quit_application


async def load_packages(packset: packages.PackagesSet) -> None:
    """Import in the list of items and styles from the packages.

    A lot of our other data is initialised here too.
    This must be called before initMain() can run.
    """
    global skybox_win, voice_win, style_win, elev_win

    for item in packset.all_obj(packages.Item):
        item_list[item.id] = Item(item)

    sky_list: list[selWinItem] = []
    voice_list: list[selWinItem] = []
    style_list: list[selWinItem] = []
    elev_list: list[selWinItem] = []

    # These don't need special-casing, and act the same.
    # The attrs are a map from selectorWin attributes, to the attribute on
    # the object.
    obj_types = [
        (sky_list, packages.Skybox, {
            '3D': 'config',  # Check if it has a config
            'COLOR': 'fog_color',
        }),
        (voice_list, packages.QuotePack, {
            'CHAR': 'chars',
            'MONITOR': 'studio',
            'TURRET': 'turret_hate',
        }),
        (style_list, packages.Style, {
            'VID': 'has_video',
        }),
        (elev_list, packages.Elevator, {
            'ORIENT': 'has_orient',
        }),
    ]

    for sel_list, obj_type, attrs in obj_types:
        # Extract the display properties out of the object, and create
        # a SelectorWin item to display with.
        for obj in sorted(packset.all_obj(obj_type), key=operator.attrgetter('selitem_data.name.token')):
            sel_list.append(selWinItem.from_data(
                obj.id,
                obj.selitem_data,
                attrs={
                    key: getattr(obj, attr_name)
                    for key, attr_name in
                    attrs.items()
                }
            ))

    def win_callback(sel_id: Optional[str]) -> None:
        """Callback for the selector windows.

        This just refreshes if the 'apply selection' option is enabled.
        """
        suggested_refresh()

    def voice_callback(voice_id: Optional[str]) -> None:
        """Special callback for the voice selector window.

        The configuration button is disabled when no music is selected.
        """
        # This might be open, so force-close it to ensure it isn't corrupt...
        voiceEditor.save()
        try:
            if voice_id is None:
                UI['conf_voice'].state(['disabled'])
                img.apply(UI['conf_voice'], ICO_GEAR_DIS)
            else:
                UI['conf_voice'].state(['!disabled'])
                img.apply(UI['conf_voice'], ICO_GEAR)
        except KeyError:
            # When first initialising, conf_voice won't exist!
            pass
        suggested_refresh()

    # Defaults match Clean Style, if not found it uses the first item.
    skybox_win = SelectorWin(
        TK_ROOT,
        sky_list,
        save_id='skyboxes',
        title=TransToken.ui('Select Skyboxes'),
        desc=TransToken.ui(
            'The skybox decides what the area outside the chamber is like. It chooses the colour '
            'of sky (seen in some items), the style of bottomless pit (if present), as well as '
            'color of "fog" (seen in larger chambers).'
        ),
        default_id='BEE2_CLEAN',
        has_none=False,
        callback=win_callback,
        attributes=[
            SelAttr.bool('3D', TransToken.ui('3D Skybox'), False),
            SelAttr.color('COLOR', TransToken.ui('Fog Color')),
        ],
    )

    voice_win = SelectorWin(
        TK_ROOT,
        voice_list,
        save_id='voicelines',
        title=TransToken.ui('Select Additional Voice Lines'),
        desc=TransToken.ui(
            'Voice lines choose which extra voices play as the player enters or exits a chamber. '
            'They are chosen based on which items are present in the map. The additional '
            '"Multiverse" Cave lines are controlled separately in Style Properties.'
        ),
        has_none=True,
        default_id='BEE2_GLADOS_CLEAN',
        none_desc=TransToken.ui('Add no extra voice lines, only Multiverse Cave if enabled.'),
        none_attrs={
            'CHAR': [TransToken.ui('<Multiverse Cave only>')],
        },
        callback=voice_callback,
        attributes=[
            SelAttr.list('CHAR', TransToken.ui('Characters'), ['??']),
            SelAttr.bool('TURRET', TransToken.ui('Turret Shoot Monitor'), False),
            SelAttr.bool('MONITOR', TransToken.ui('Monitor Visuals'), False),
        ],
    )

    style_win = SelectorWin(
        TK_ROOT,
        style_list,
        save_id='styles',
        default_id='BEE2_CLEAN',
        title=TransToken.ui('Select Style'),
        desc=TransToken.ui(
            'The Style controls many aspects of the map. It decides the materials used for walls, '
            'the appearance of entrances and exits, the design for most items as well as other '
            'settings.\n\nThe style broadly defines the time period a chamber is set in.'
        ),
        has_none=False,
        has_def=False,
        # Selecting items changes much of the gui - don't allow when other
        # things are open...
        modal=True,
        # callback set in the main initialisation function..
        attributes=[
            SelAttr.bool('VID', TransToken.ui('Elevator Videos'), default=True),
        ]
    )

    elev_win = SelectorWin(
        TK_ROOT,
        elev_list,
        save_id='elevators',
        title=TransToken.ui('Select Elevator Video'),
        desc=TransToken.ui(
            'Set the video played on the video screens in modern Aperture elevator rooms. Not all '
            'styles feature these. If set to "None", a random video will be selected each time the '
            'map is played, like in the default PeTI.'
        ),
        readonly_desc=TransToken.ui('This style does not have a elevator video screen.'),
        has_none=True,
        has_def=True,
        none_icon=img.Handle.builtin('BEE2/random', 96, 96),
        none_name=TransToken.ui('Random'),
        none_desc=TransToken.ui('Choose a random video.'),
        callback=win_callback,
        attributes=[
            SelAttr.bool('ORIENT', TransToken.ui('Multiple Orientations')),
        ]
    )


def current_style() -> packages.Style:
    """Return the currently selected style."""
    return packages.LOADED.obj_by_id(packages.Style, selected_style)


def reposition_panes() -> None:
    """Position all the panes in the default places around the main window."""
    comp_win = CompilerPane.window
    opt_win = windows['opt']
    pal_win = windows['pal']
    # The x-pos of the right side of the main window
    xpos = min(
        TK_ROOT.winfo_screenwidth()
        - itemconfig.window.winfo_reqwidth(),

        TK_ROOT.winfo_rootx()
        + TK_ROOT.winfo_reqwidth()
        + 25
        )
    # The x-pos for the palette and compiler panes
    pal_x = TK_ROOT.winfo_rootx() - comp_win.winfo_reqwidth() - 25
    pal_win.move(
        x=pal_x,
        y=(TK_ROOT.winfo_rooty() - 50),
        height=max(
            TK_ROOT.winfo_reqheight() -
            comp_win.winfo_reqheight() -
            25,
            30,
        ),
        width=comp_win.winfo_reqwidth(),
    )
    comp_win.move(
        x=pal_x,
        y=pal_win.winfo_rooty() + pal_win.winfo_reqheight(),
    )
    opt_win.move(
        x=xpos,
        y=TK_ROOT.winfo_rooty()-40,
        width=itemconfig.window.winfo_reqwidth())
    itemconfig.window.move(
        x=xpos,
        y=TK_ROOT.winfo_rooty() + opt_win.winfo_reqheight() + 25)


def reset_panes() -> None:
    """Reset the position of all panes."""
    reposition_panes()
    windows['pal'].save_conf()
    windows['opt'].save_conf()
    itemconfig.window.save_conf()
    CompilerPane.window.save_conf()


def suggested_refresh() -> None:
    """Enable or disable the suggestion setting button."""
    if 'suggested_style' in UI:
        windows = [
            voice_win,
            skybox_win,
            elev_win,
        ]
        windows.extend(music_conf.WINDOWS.values())
        if all(win.is_suggested() for win in windows):
            UI['suggested_style'].state(['disabled'])
        else:
            UI['suggested_style'].state(['!disabled'])


async def export_editoritems(pal_ui: paletteUI.PaletteUI, bar: MenuBar) -> None:
    """Export the selected Items and Style into the chosen game."""
    # Disable, so you can't double-export.
    UI['pal_export'].state(('disabled',))
    bar.set_export_allowed(False)
    await tk_tools.wait_eventloop()
    try:
        # Convert IntVar to boolean, and only export values in the selected style
        chosen_style = current_style()

        # The chosen items on the palette
        pal_data = [(it.id, it.subKey) for it in pal_picked]

        item_versions = {
            it_id: item.selected_ver
            for it_id, item in
            item_list.items()
        }

        item_properties = {
            it_id: {
                key[5:]: value
                for key, value in
                section.items() if
                key.startswith('prop_')
            }
            for it_id, section in
            item_opts.items()
        }
        conf = config.APP.get_cur_conf(config.gen_opts.GenOptions)

        success, vpk_success = await gameMan.selected_game.export(
            style=chosen_style,
            selected_objects={
                # Specify the 'chosen item' for each object type
                packages.Music: music_conf.export_data(packages.LOADED),
                packages.Skybox: skybox_win.chosen_id,
                packages.QuotePack: voice_win.chosen_id,
                packages.Elevator: elev_win.chosen_id,

                packages.Item: (pal_data, item_versions, item_properties),
                packages.StyleVar: StyleVarPane.export_data(chosen_style),
                packages.Signage: signage_ui.export_data(),

                # The others don't have one, so it defaults to None.
            },
            should_refresh=not conf.preserve_resources,
        )

        if not success:
            return

        try:
            last_export = pal_ui.palettes[paletteUI.UUID_EXPORT]
        except KeyError:
            last_export = pal_ui.palettes[paletteUI.UUID_EXPORT] = paletteUI.Palette(
                '',
                pal_data,
                # This makes it lookup the translated name
                # instead of using a configured one.
                trans_name='LAST_EXPORT',
                uuid=paletteUI.UUID_EXPORT,
                readonly=True,
            )
        last_export.pos = pal_data
        last_export.save(ignore_readonly=True)

        # Save the configs since we're writing to disk lots anyway.
        GEN_OPTS.save_check()
        item_opts.save_check()
        config.APP.write_file()

        message = TRANS_EXPORTED if vpk_success else TRANS_EXPORTED_NO_VPK

        if conf.launch_after_export or conf.after_export is not config.gen_opts.AfterExport.NORMAL:
            do_action = tk_tools.askyesno(
                TRANS_EXPORTED_TITLE,
                optionWindow.AFTER_EXPORT_TEXT[
                    conf.after_export, conf.launch_after_export,
                ].format(msg=message),
            )
        else:  # No action to do, so just show an OK.
            tk_tools.showinfo(TRANS_EXPORTED_TITLE, message)
            do_action = False

        # Do the desired action - if quit, we don't bother to update UI.
        if do_action:
            # Launch first so quitting doesn't affect this.
            if conf.launch_after_export:
                gameMan.selected_game.launch()

            if conf.after_export is AfterExport.NORMAL:
                pass
            elif conf.after_export is AfterExport.MINIMISE:
                TK_ROOT.iconify()
            elif conf.after_export is AfterExport.QUIT:
                quit_application()
                # We never return from this.
            else:
                raise ValueError(f'Unknown action "{conf.after_export}"')

        # Select the last_export palette, so reloading loads this item selection.
        # But leave it at the current palette, if it's unmodified.
        if pal_ui.selected.pos != pal_data:
            pal_ui.select_palette(paletteUI.UUID_EXPORT)
            pal_ui.update_state()

        # Re-fire this, so we clear the '*' on buttons if extracting cache.
        await gameMan.EVENT_BUS(None, gameMan.selected_game)
    finally:
        UI['pal_export'].state(('!disabled',))
        bar.set_export_allowed(True)


def set_disp_name(item: PalItem, e=None) -> None:
    """Callback to display the name of the item."""
    localisation.set_text(UI['pre_disp_name'], item.name)


def clear_disp_name(e=None) -> None:
    """Callback to reset the item name."""
    localisation.set_text(UI['pre_disp_name'], TransToken.BLANK)


def conv_screen_to_grid(x: float, y: float) -> Tuple[int, int]:
    """Returns the location of the item hovered over on the preview pane."""
    return (
        (x-UI['pre_bg_img'].winfo_rootx()-8) // 65,
        (y-UI['pre_bg_img'].winfo_rooty()-32) // 65,
    )


def drag_start(drag_item: PalItem, e: tk.Event) -> None:
    """Start dragging a palette item."""
    drag_win = windows['drag_win']
    drag_win.drag_item = drag_item
    set_disp_name(drag_item)
    snd.fx('config')
    drag_win.passed_over_pal = False
    if drag_item.is_pre:  # is the cursor over the preview pane?
        drag_item.kill()
        UI['pre_moving'].place(
            x=drag_item.pre_x*65 + 4,
            y=drag_item.pre_y*65 + 32,
        )
        drag_win.from_pal = True

        for item in pal_picked:
            if item.id == drag_win.drag_item.id:
                item.load_data()

        # When dragging off, switch to the single-only icon
        img.apply(UI['drag_lbl'], drag_item.item.get_icon(
            drag_item.subKey,
            allow_single=False,
        ))
    else:
        drag_win.from_pal = False
        img.apply(UI['drag_lbl'], drag_item.item.get_icon(
            drag_item.subKey,
            allow_single=True,
            single_num=0,
        ))
    drag_win.deiconify()
    drag_win.lift()
    # grab makes this window the only one to receive mouse events, so
    # it is guaranteed that it'll drop when the mouse is released.
    drag_win.grab_set_global()
    # NOTE: _global means no other programs can interact, make sure
    # it's released eventually or you won't be able to quit!
    drag_move(e)  # move to correct position
    drag_win.bind(tk_tools.EVENTS['LEFT_MOVE'], drag_move)
    UI['pre_sel_line'].lift()


def drag_stop(e: tk.Event) -> None:
    """User released the mouse button, complete the drag."""
    drag_win = windows['drag_win']

    if drag_win.drag_item is None:
        # We aren't dragging, ignore the event.
        return

    drag_win.withdraw()
    drag_win.unbind("<B1-Motion>")
    drag_win.grab_release()
    clear_disp_name()
    UI['pre_sel_line'].place_forget()
    UI['pre_moving'].place_forget()
    snd.fx('config')

    pos_x, pos_y = conv_screen_to_grid(e.x_root, e.y_root)
    ind = pos_x + pos_y * 4

    # this prevents a single click on the picker from clearing items
    # off the palette
    if drag_win.passed_over_pal:
        # is the cursor over the preview pane?
        if 0 <= pos_x < 4 and 0 <= pos_y < 8:
            drag_win.drag_item.clear()  # wipe duplicates off the palette first
            new_item = drag_win.drag_item.copy(frames['preview'])
            new_item.is_pre = True
            if ind >= len(pal_picked):
                pal_picked.append(new_item)
            else:
                pal_picked.insert(ind, new_item)
            # delete the item - it's fallen off the palette
            if len(pal_picked) > 32:
                pal_picked.pop().kill()
        else:  # drop the item
            if drag_win.from_pal:
                # Only remove if we started on the palette
                drag_win.drag_item.clear()
            snd.fx('delete')

        flow_preview()  # always refresh
    drag_win.drag_item = None


def drag_move(e: tk.Event) -> None:
    """Update the position of dragged items as they move around."""
    drag_win = windows['drag_win']

    if drag_win.drag_item is None:
        # We aren't dragging, ignore the event.
        return

    set_disp_name(drag_win.drag_item)
    drag_win.geometry('+'+str(e.x_root-32)+'+'+str(e.y_root-32))
    pos_x, pos_y = conv_screen_to_grid(e.x_root, e.y_root)
    if 0 <= pos_x < 4 and 0 <= pos_y < 8:
        drag_win['cursor'] = tk_tools.Cursors.MOVE_ITEM
        UI['pre_sel_line'].place(x=pos_x*65+3, y=pos_y*65+33)
        if not drag_win.passed_over_pal:
            # If we've passed over the palette, replace identical items
            # with movement icons to indicate they will move to the new location
            for item in pal_picked:
                if item.id == drag_win.drag_item.id and item.subKey == drag_win.drag_item.subKey:
                    # We haven't removed the original, so we don't need the
                    # special label for this.
                    # The group item refresh will return this if nothing
                    # changes.
                    img.apply(item.label, ICO_MOVING)
                    break

        drag_win.passed_over_pal = True
    else:
        if drag_win.from_pal and drag_win.passed_over_pal:
            drag_win['cursor'] = tk_tools.Cursors.DESTROY_ITEM
        else:
            drag_win['cursor'] = tk_tools.Cursors.INVALID_DRAG
        UI['pre_sel_line'].place_forget()


def drag_fast(drag_item: PalItem, e: tk.Event) -> None:
    """Implement shift-clicking.

     When shift-clicking, an item will be immediately moved to the
     palette or deleted from it.
    """
    pos_x, pos_y = conv_screen_to_grid(e.x_root, e.y_root)
    drag_item.clear()
    # Is the cursor over the preview pane?
    if 0 <= pos_x < 4:
        snd.fx('delete')
        flow_picker()
    else:  # over the picker
        if len(pal_picked) < 32:  # can't copy if there isn't room
            snd.fx('config')
            new_item = drag_item.copy(frames['preview'])
            new_item.is_pre = True
            pal_picked.append(new_item)
        else:
            snd.fx('error')
    flow_preview()


async def set_palette(chosen_pal: paletteUI.Palette) -> None:
    """Select a palette."""
    pal_clear()
    for item, sub in chosen_pal.pos:
        try:
            item_group = item_list[item]
        except KeyError:
            LOGGER.warning('Unknown item "{}"! for palette', item)
            continue

        if sub not in item_group.visual_subtypes:
            LOGGER.warning(
                'Palette had incorrect subtype {} for "{}"! Valid subtypes: {}!',
                item, sub, item_group.visual_subtypes,
            )
            continue

        pal_picked.append(PalItem(
            frames['preview'],
            item_list[item],
            sub,
            is_pre=True,
        ))

    if chosen_pal.settings is not None:
        LOGGER.info('Settings: {}', chosen_pal.settings)
        await config.apply_pal_conf(chosen_pal.settings)

    flow_preview()


def pal_clear() -> None:
    """Empty the palette."""
    for item in pal_picked[:]:
        item.kill()
    flow_preview()


def pal_shuffle() -> None:
    """Set the palette to a list of random items."""
    mandatory_unlocked = StyleVarPane.mandatory_unlocked()

    if len(pal_picked) == 32:
        return

    palette_set = {
        item.id
        for item in pal_picked
    }

    # Use a set to eliminate duplicates.
    shuff_items = list({
        item.id
        # Only consider items not already on the palette,
        # obey the mandatory item lock and filters.
        for item in pal_items
        if item.id not in palette_set
        if mandatory_unlocked or not item.needs_unlock
        if cur_filter is None or (item.id, item.subKey) in cur_filter
    })

    random.shuffle(shuff_items)

    for item_id in shuff_items[:32-len(pal_picked)]:
        item = item_list[item_id]
        pal_picked.append(PalItem(
            frames['preview'],
            item,
            # Pick a random available palette icon.
            sub=random.choice(item.visual_subtypes),
            is_pre=True,
        ))
    flow_preview()


async def init_option(
    pane: SubPane.SubPane,
    pal_ui: paletteUI.PaletteUI,
    export: Callable[[], object],
) -> None:
    """Initialise the options pane."""
    pane.columnconfigure(0, weight=1)
    pane.rowconfigure(0, weight=1)

    frame = ttk.Frame(pane)
    frame.grid(row=0, column=0, sticky='nsew')
    frame.columnconfigure(0, weight=1)

    pal_save = ttk.Button(frame, command=pal_ui.event_save)
    localisation.set_text(pal_save, TransToken.ui("Save Palette..."))
    pal_save.grid(row=0, sticky="EW", padx=5)
    pal_ui.save_btn_state = pal_save.state

    localisation.set_text(
        ttk.Button(frame, command=pal_ui.event_save_as),
        TransToken.ui("Save Palette As..."),
    ).grid(row=1, sticky="EW", padx=5)

    pal_ui.make_option_checkbox(frame).grid(row=2, sticky="EW", padx=5)

    ttk.Separator(frame, orient='horizontal').grid(row=3, sticky="EW")

    UI['pal_export'] = ttk.Button(frame, command=export)
    UI['pal_export'].state(('disabled',))
    UI['pal_export'].grid(row=4, sticky="EW", padx=5)

    async def game_changed(game: gameMan.Game) -> None:
        """When the game changes, update this button."""
        localisation.set_text(UI['pal_export'], game.get_export_text())

    await gameMan.EVENT_BUS.register_and_prime(None, gameMan.Game, game_changed)

    props = ttk.Frame(frame, width="50")
    props.columnconfigure(1, weight=1)
    props.grid(row=5, sticky="EW")

    music_frame = ttk.Labelframe(props)
    localisation.set_text(music_frame, TransToken.ui('Music: '))

    async with trio.open_nursery() as nursery:
        nursery.start_soon(music_conf.make_widgets, packages.LOADED, music_frame, pane)
    music_win = music_conf.WINDOWS[music_conf.MusicChannel.BASE]

    def suggested_style_set() -> None:
        """Set music, skybox, voices, etc to the settings defined for a style."""
        win_types = (voice_win, music_win, skybox_win, elev_win)
        has_suggest = False
        for win in win_types:
            win.sel_suggested()
            if win.can_suggest():
                has_suggest = True
        UI['suggested_style'].state(('!disabled', ) if has_suggest else ('disabled', ))

    def suggested_style_mousein(_: tk.Event) -> None:
        """When mousing over the button, show the suggested items."""
        for win in (voice_win, music_win, skybox_win, elev_win):
            win.rollover_suggest()

    def suggested_style_mouseout(_: tk.Event) -> None:
        """Return text to the normal value on mouseout."""
        for win in (voice_win, music_win, skybox_win, elev_win):
            win.set_disp()

    UI['suggested_style'] = sugg_btn =  ttk.Button(props, command=suggested_style_set)
    # '\u2193' is the downward arrow symbol.
    localisation.set_text(sugg_btn, TransToken.ui(
        "{down_arrow} Use Suggested {down_arrow}"
    ).format(down_arrow='\u2193'))
    sugg_btn.grid(row=1, column=1, columnspan=2, sticky="EW", padx=0)
    sugg_btn.bind('<Enter>', suggested_style_mousein)
    sugg_btn.bind('<Leave>', suggested_style_mouseout)

    def configure_voice() -> None:
        """Open the voiceEditor window to configure a Quote Pack."""
        try:
            chosen_voice = packages.LOADED.obj_by_id(packages.QuotePack, voice_win.chosen_id)
        except KeyError:
            pass
        else:
            voiceEditor.show(chosen_voice)
    for ind, name in enumerate([
            TransToken.ui("Style: "),
            None,
            TransToken.ui("Voice: "),
            TransToken.ui("Skybox: "),
            TransToken.ui("Elev Vid: "),
            ]):
        if name is None:
            # This is the "Suggested" button!
            continue
        localisation.set_text(ttk.Label(props), name).grid(row=ind)

    voice_frame = ttk.Frame(props)
    voice_frame.columnconfigure(1, weight=1)
    UI['conf_voice'] = ttk.Button(
        voice_frame,
        command=configure_voice,
        width=8,
    )
    UI['conf_voice'].grid(row=0, column=0, sticky='NS')
    img.apply(UI['conf_voice'], ICO_GEAR_DIS)
    tooltip.add_tooltip(
        UI['conf_voice'],
        TransToken.ui('Enable or disable particular voice lines, to prevent them from being added.'),
    )

    if utils.WIN:
        # On Windows, the buttons get inset on the left a bit. Inset everything
        # else to adjust.
        left_pad = (1, 0)
    else:
        left_pad = (0, 0)

    # Make all the selector window textboxes
    (await style_win.widget(props)).grid(row=0, column=1, sticky='EW', padx=left_pad)
    # row=1: Suggested.
    voice_frame.grid(row=2, column=1, sticky='EW')
    (await skybox_win.widget(props)).grid(row=3, column=1, sticky='EW', padx=left_pad)
    (await elev_win.widget(props)).grid(row=4, column=1, sticky='EW', padx=left_pad)
    music_frame.grid(row=5, column=0, sticky='EW', columnspan=2)

    (await voice_win.widget(voice_frame)).grid(row=0, column=1, sticky='EW', padx=left_pad)

    if tk_tools.USE_SIZEGRIP:
        sizegrip = ttk.Sizegrip(props, cursor=tk_tools.Cursors.STRETCH_HORIZ)
        sizegrip.grid(row=2, column=5, rowspan=2, sticky="NS")


def flow_preview() -> None:
    """Position all the preview icons based on the array.

    Run to refresh if items are moved around.
    """
    for i, item in enumerate(pal_picked):
        # these can be referred to to figure out where it is
        item.pre_x = i % 4
        item.pre_y = i // 4
        item.label.place(x=(i % 4*65 + 4), y=(i // 4*65 + 32))
        # Check to see if this should use the single-icon
        item.load_data()
        item.label.lift()

    item_count = len(pal_picked)
    for ind, fake in enumerate(pal_picked_fake):
        if ind < item_count:
            fake.place_forget()
        else:
            fake.place(x=(ind % 4*65+4), y=(ind//4*65+32))
            fake.lift()
    UI['pre_sel_line'].lift()


def init_preview(f: Union[tk.Frame, ttk.Frame]) -> None:
    """Generate the preview pane.

     This shows the items that will export to the palette.
    """
    UI['pre_bg_img'] = tk.Label(f, bg=ItemsBG)
    UI['pre_bg_img'].grid(row=0, column=0)
    img.apply(UI['pre_bg_img'], img.Handle.builtin('BEE2/menu', 271, 573))

    UI['pre_disp_name'] = ttk.Label(
        f,
        text="",
        style='BG.TLabel',
        )
    UI['pre_disp_name'].place(x=10, y=554)

    UI['pre_sel_line'] = tk.Label(
        f,
        bg="#F0F0F0",
        borderwidth=0,
        relief="solid",
        )
    img.apply(UI['pre_sel_line'], img.Handle.builtin('BEE2/sel_bar', 4, 64))
    pal_picked_fake.extend([
        img.apply(ttk.Label(frames['preview']), IMG_BLANK)
        for _ in range(32)
    ])

    UI['pre_moving'] = ttk.Label(f)
    img.apply(UI['pre_moving'], ICO_MOVING)

    flow_preview()


def init_picker(f: Union[tk.Frame, ttk.Frame]) -> None:
    """Construct the frame holding all the items."""
    global frmScroll, pal_canvas
    localisation.set_text(
        ttk.Label(f, anchor="center"),
        TransToken.ui("All Items: "),
    ).grid(row=0, column=0, sticky="EW")
    UI['picker_frame'] = cframe = ttk.Frame(f, borderwidth=4, relief="sunken")
    cframe.grid(row=1, column=0, sticky="NSEW")
    f.rowconfigure(1, weight=1)
    f.columnconfigure(0, weight=1)

    pal_canvas = tk.Canvas(cframe)
    # need to use a canvas to allow scrolling
    pal_canvas.grid(row=0, column=0, sticky="NSEW")
    cframe.rowconfigure(0, weight=1)
    cframe.columnconfigure(0, weight=1)

    scroll = tk_tools.HidingScroll(
        cframe,
        orient=tk.VERTICAL,
        command=pal_canvas.yview,
    )
    scroll.grid(column=1, row=0, sticky="NS")
    pal_canvas['yscrollcommand'] = scroll.set

    # add another frame inside to place labels on
    frmScroll = ttk.Frame(pal_canvas)
    pal_canvas.create_window(1, 1, window=frmScroll, anchor="nw")

    # Create the items in the palette.
    # Sort by item ID, and then group by package ID.
    # Reverse sort packages so 'Valve' appears at the top..
    items = sorted(item_list.values(), key=operator.attrgetter('id'))
    items.sort(key=operator.attrgetter('pak_id'), reverse=True)

    for item in items:
        for i, subtype in enumerate(item.data.editor.subtypes):
            if subtype.pal_icon or subtype.pal_name:
                pal_items.append(PalItem(frmScroll, item, sub=i, is_pre=False))

    f.bind("<Configure>", flow_picker)


def flow_picker(e=None) -> None:
    """Update the picker box so all items are positioned corrctly.

    Should be run (e arg is ignored) whenever the items change, or the
    window changes shape.
    """
    frmScroll.update_idletasks()
    frmScroll['width'] = pal_canvas.winfo_width()
    mandatory_unlocked = StyleVarPane.mandatory_unlocked()

    width = (pal_canvas.winfo_width() - 10) // 65
    if width < 1:
        width = 1  # we got way too small, prevent division by zero

    i = 0
    for item in pal_items:
        if item.needs_unlock and not mandatory_unlocked:
            visible = False
        elif cur_filter is None:
            visible = True
        else:
            visible = (item.item.id, item.subKey) in cur_filter

        if visible:
            item.is_pre = False
            item.label.place(
                x=((i % width) * 65 + 1),
                y=((i // width) * 65 + 1),
                )
            i += 1
        else:
            item.label.place_forget()

    num_items = i

    height = int(math.ceil(num_items / width)) * 65 + 2
    pal_canvas['scrollregion'] = (0, 0, width * 65, height)
    frmScroll['height'] = height

    # Now, add extra blank items on the end to finish the grid nicely.
    # pal_items_fake allows us to recycle existing icons.
    last_row = num_items % width
    # Special case, don't add a full row if it's exactly the right count.
    extra_items = (width - last_row) if last_row != 0 else 0

    y = (num_items // width)*65 + 1
    for i in range(extra_items):
        if i not in pal_items_fake:
            pal_items_fake.append(img.apply(ttk.Label(frmScroll), IMG_BLANK))
        pal_items_fake[i].place(x=((i + last_row) % width)*65 + 1, y=y)

    for item in pal_items_fake[extra_items:]:
        item.place_forget()


def init_drag_icon() -> None:
    """Create the window for rendering held items."""
    drag_win = tk.Toplevel(TK_ROOT)
    # this prevents stuff like the title bar, normal borders etc from
    # appearing in this window.
    drag_win.overrideredirect(True)
    drag_win.resizable(False, False)
    drag_win.withdraw()
    drag_win.transient(master=TK_ROOT)
    drag_win.withdraw()  # starts hidden
    drag_win.bind(tk_tools.EVENTS['LEFT_RELEASE'], drag_stop)
    UI['drag_lbl'] = ttk.Label(drag_win)
    img.apply(UI['drag_lbl'], IMG_BLANK)
    UI['drag_lbl'].grid(row=0, column=0)
    windows['drag_win'] = drag_win

    drag_win.passed_over_pal = False  # has the cursor passed over the palette
    drag_win.from_pal = False  # are we dragging a palette item?
    drag_win.drag_item = None  # the item currently being moved


async def set_game(game: 'gameMan.Game') -> None:
    """Callback for when the game is changed.

    This updates the title bar to match, and saves it into the config.
    """
    localisation.set_win_title(TK_ROOT, TRANS_MAIN_TITLE.format(version=utils.BEE_VERSION, game=game.name))
    config.APP.store_conf(LastSelected(game.name), 'game')


def refresh_palette_icons() -> None:
    """Refresh all displayed palette icons."""
    for pal_item in itertools.chain(pal_picked, pal_items):
        pal_item.load_data()


async def init_windows() -> None:
    """Initialise all windows and panes.

    """
    def export() -> None:
        """Export the palette, passing the required UI objects."""
        background_run(export_editoritems, pal_ui, menu_bar)

    menu_bar = MenuBar(
        TK_ROOT,
        quit_app=quit_application,
        export=export,
    )
    TK_ROOT.maxsize(
        width=TK_ROOT.winfo_screenwidth(),
        height=TK_ROOT.winfo_screenheight(),
    )
    TK_ROOT.protocol("WM_DELETE_WINDOW", quit_application)
    gameMan.EVENT_BUS.register(None, gameMan.Game, set_game)
    # Initialise the above and the menu bar.
    await gameMan.EVENT_BUS(None, gameMan.Game)

    ui_bg = tk.Frame(TK_ROOT, bg=ItemsBG, name='bg')
    ui_bg.grid(row=0, column=0, sticky='NSEW')
    TK_ROOT.columnconfigure(0, weight=1)
    TK_ROOT.rowconfigure(0, weight=1)
    ui_bg.rowconfigure(0, weight=1)

    style = ttk.Style()
    # Custom button style with correct background
    # Custom label style with correct background
    style.configure('BG.TButton', background=ItemsBG)
    style.configure('Preview.TLabel', background='#F4F5F5')

    frames['preview'] = tk.Frame(ui_bg, bg=ItemsBG, name='preview')
    frames['preview'].grid(
        row=0, column=3,
        sticky="NW",
        padx=(2, 5), pady=5,
    )
    init_preview(frames['preview'])
    frames['preview'].update_idletasks()
    TK_ROOT.minsize(
        width=frames['preview'].winfo_reqwidth()+200,
        height=frames['preview'].winfo_reqheight()+5,
    )  # Prevent making the window smaller than the preview pane

    await trio.sleep(0)
    loader.step('UI', 'preview')

    ttk.Separator(ui_bg, orient='vertical').grid(
        row=0, column=4,
        sticky="NS",
        padx=10, pady=10,
    )

    picker_split_frame = tk.Frame(ui_bg, bg=ItemsBG, name='picker_split')
    picker_split_frame.grid(row=0, column=5, sticky="NSEW", padx=5, pady=5)
    ui_bg.columnconfigure(5, weight=1)

    # This will sit on top of the palette section, spanning from left
    # to right
    search_frame = ttk.Frame(
        picker_split_frame,
        name='searchbar',
        padding=5,
        borderwidth=0,
        relief="raised",
    )
    search_frame.grid(row=0, column=0, sticky='ew')

    def update_filter(new_filter: Optional[Set[Tuple[str, int]]]) -> None:
        """Refresh filtered items whenever it's changed."""
        global cur_filter
        cur_filter = new_filter
        flow_picker()

    item_search.init(search_frame, update_filter)

    await trio.sleep(0)
    loader.step('UI', 'filter')

    frames['picker'] = ttk.Frame(
        picker_split_frame,
        name='picker',
        padding=5,
        borderwidth=4,
        relief="raised",
    )
    frames['picker'].grid(row=1, column=0, sticky="NSEW")
    picker_split_frame.rowconfigure(1, weight=1)
    picker_split_frame.columnconfigure(0, weight=1)
    init_picker(frames['picker'])

    await trio.sleep(0)
    loader.step('UI', 'picker')

    frames['toolMenu'] = tk.Frame(
        frames['preview'],
        name='toolbar',
        bg=ItemsBG,
        width=192,
        height=26,
        borderwidth=0,
        )
    frames['toolMenu'].place(x=73, y=2)

    windows['pal'] = SubPane.SubPane(
        TK_ROOT,
        title=TransToken.ui('Palettes'),
        name='pal',
        menu_bar=menu_bar.view_menu,
        resize_x=True,
        resize_y=True,
        tool_frame=frames['toolMenu'],
        tool_img='icons/win_palette',
        tool_col=1,
    )

    pal_frame = ttk.Frame(windows['pal'], name='pal_frame')
    pal_frame.grid(row=0, column=0, sticky='NSEW')
    windows['pal'].columnconfigure(0, weight=1)
    windows['pal'].rowconfigure(0, weight=1)

    pal_ui = paletteUI.PaletteUI(
        pal_frame, menu_bar.pal_menu,
        cmd_clear=pal_clear,
        cmd_shuffle=pal_shuffle,
        get_items=lambda: [(it.id, it.subKey) for it in pal_picked],
        set_items=set_palette,
    )

    TK_ROOT.bind_all(tk_tools.KEY_SAVE, lambda e: pal_ui.event_save())
    TK_ROOT.bind_all(tk_tools.KEY_SAVE_AS, lambda e: pal_ui.event_save_as())
    TK_ROOT.bind_all(tk_tools.KEY_EXPORT, lambda e: background_run(export_editoritems, pal_ui, menu_bar))

    await trio.sleep(0)
    loader.step('UI', 'palette')

    packageMan.make_window()

    await trio.sleep(0)
    loader.step('UI', 'packageman')

    windows['opt'] = SubPane.SubPane(
        TK_ROOT,
        title=TransToken.ui('Export Options'),
        name='opt',
        menu_bar=menu_bar.view_menu,
        resize_x=True,
        tool_frame=frames['toolMenu'],
        tool_img='icons/win_options',
        tool_col=2,
    )

    async with trio.open_nursery() as nurs:
        nurs.start_soon(init_option, windows['opt'], pal_ui, export)
    loader.step('UI', 'options')

    async with trio.open_nursery() as nurs:
        nurs.start_soon(itemconfig.make_pane, frames['toolMenu'], menu_bar.view_menu, flow_picker)
    loader.step('UI', 'itemvar')

    async with trio.open_nursery() as nurs:
        corridor = corridor_selector.Selector(packages.LOADED)
        nurs.start_soon(CompilerPane.make_pane, frames['toolMenu'], menu_bar.view_menu, corridor)
    async with trio.open_nursery() as nurs:
        nurs.start_soon(corridor.refresh)
    loader.step('UI', 'compiler')

    UI['shuffle_pal'] = SubPane.make_tool_button(
        frame=frames['toolMenu'],
        img='icons/shuffle_pal',
        command=pal_shuffle,
    )
    UI['shuffle_pal'].grid(
        row=0,
        column=0,
        padx=((2, 10) if utils.MAC else (2, 20)),
    )
    tooltip.add_tooltip(
        UI['shuffle_pal'],
        TransToken.ui('Fill empty spots in the palette with random items.'),
    )

    # Make scrollbar work globally
    tk_tools.add_mousewheel(pal_canvas, TK_ROOT)

    # When clicking on any window hide the context window
    tk_tools.bind_leftclick(TK_ROOT, contextWin.hide_context)
    tk_tools.bind_leftclick(itemconfig.window, contextWin.hide_context)
    tk_tools.bind_leftclick(CompilerPane.window, contextWin.hide_context)
    tk_tools.bind_leftclick(corridor.win, contextWin.hide_context)
    tk_tools.bind_leftclick(windows['opt'], contextWin.hide_context)
    tk_tools.bind_leftclick(windows['pal'], contextWin.hide_context)

    await trio.sleep(0)
    backup_win.init_toplevel()
    await trio.sleep(0)
    loader.step('UI', 'backup')
    voiceEditor.init_widgets()
    await trio.sleep(0)
    loader.step('UI', 'voiceline')
    contextWin.init_widgets()
    loader.step('UI', 'contextwin')
    await optionWindow.init_widgets(
        unhide_palettes=pal_ui.reset_hidden_palettes,
        reset_all_win=reset_panes,
    )
    loader.step('UI', 'optionwindow')
    init_drag_icon()
    loader.step('UI', 'drag_icon')
    await trio.sleep(0)

    # Load to properly apply config settings, then save to ensure
    # the file has any defaults applied.
    optionWindow.load()
    optionWindow.save()

    TK_ROOT.deiconify()  # show it once we've loaded everything
    windows['pal'].deiconify()
    windows['opt'].deiconify()
    itemconfig.window.deiconify()
    CompilerPane.window.deiconify()

    if utils.MAC:
        TK_ROOT.lift()  # Raise to the top of the stack

    await trio.sleep(0.1)

    # Position windows according to remembered settings:
    try:
        main_win_state = config.APP.get_cur_conf(WindowState, 'main_window')
    except KeyError:
        # We don't have a config, position the window ourselves
        # move the main window if needed to allow room for palette
        if TK_ROOT.winfo_rootx() < windows['pal'].winfo_reqwidth() + 50:
            TK_ROOT.geometry(
                f'+{windows["pal"].winfo_reqwidth() + 50}+{TK_ROOT.winfo_rooty()}'
            )
        else:
            TK_ROOT.geometry(f'+{TK_ROOT.winfo_rootx()}+{TK_ROOT.winfo_rooty()}')
    else:
        start_x, start_y = tk_tools.adjust_inside_screen(
            main_win_state.x, main_win_state.y,
            win=TK_ROOT,
        )
        TK_ROOT.geometry(f'+{start_x}+{start_y}')
    await tk_tools.wait_eventloop()

    # First move to default positions, then load the config.
    # If the config is valid, this will move them to user-defined
    # positions.
    reposition_panes()
    itemconfig.window.load_conf()
    CompilerPane.window.load_conf()
    windows['opt'].load_conf()
    windows['pal'].load_conf()

    async def enable_export() -> None:
        """Enable exporting only after all packages are loaded."""
        for cls in packages.OBJ_TYPES.values():
            await packages.LOADED.ready(cls).wait()
        UI['pal_export'].state(('!disabled',))
        menu_bar.set_export_allowed(True)

    background_run(enable_export)

    def style_select_callback(style_id: Optional[str]) -> None:
        """Callback whenever a new style is chosen."""
        global selected_style
        assert style_id is not None, "Style ID must be provided"
        selected_style = style_id

        style_obj = current_style()
        for item in item_list.values():
            item.load_data()
        refresh_palette_icons()

        # Update variant selectors on the itemconfig pane
        for item_id, func in itemconfig.ITEM_VARIANT_LOAD:
            func()

        # Disable this if the style doesn't have elevators
        elev_win.readonly = not style_obj.has_video

        signage_ui.style_changed(style_obj)
        item_search.rebuild_database()

        sugg = style_obj.suggested
        win_types = (
            voice_win,
            music_conf.WINDOWS[consts.MusicChannel.BASE],
            skybox_win,
            elev_win,
        )
        for win, sugg_val in zip(win_types, sugg):
            win.set_suggested(sugg_val)
        suggested_refresh()
        StyleVarPane.refresh(style_obj)
        corridor.load_corridors(packages.LOADED)
        background_run(corridor.refresh)

    style_win.callback = style_select_callback
    style_select_callback(style_win.chosen_id)
    await set_palette(pal_ui.selected)
    pal_ui.update_state()
