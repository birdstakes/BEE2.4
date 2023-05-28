"""Handles the UI required for saving and loading palettes."""
from __future__ import annotations
from typing import Awaitable, Callable
from uuid import UUID

from tkinter import ttk
import tkinter as tk

import srctools.logger

from app.paletteLoader import Palette, ItemPos, VertInd, HorizInd, COORDS, VERT, HORIZ
from app import background_run, localisation, tk_tools, paletteLoader, TK_ROOT, img
from consts import PALETTE_FORCE_SHOWN, UUID_BLANK, UUID_EXPORT, UUID_PORTAL2
from config.palette import PaletteState
from transtoken import TransToken
from ui_tk.img import tkImg, TKImages
import config


LOGGER = srctools.logger.get_logger(__name__)
TREE_TAG_GROUPS = 'pal_group'
TREE_TAG_PALETTES = 'palette'
ICO_GEAR = img.Handle.sprite('icons/gear', 10, 10)

# Re-export paletteLoader values for convenience.
__all__ = [
    'PaletteUI',
    'Palette', 'ItemPos', 'VertInd', 'HorizInd', 'VERT', 'HORIZ', 'COORDS',
    'UUID', 'UUID_EXPORT', 'UUID_PORTAL2', 'UUID_BLANK',
]
TRANS_DELETE = TransToken.ui("Delete")
TRANS_HIDE = TransToken.ui("Hide")
TRANS_DELETE_NAMED = TransToken.ui('Delete Palette "{name}"')
TRANS_HIDE_NAMED = TransToken.ui('Hide Palette "{name}"')
TRANS_SHOULD_DELETE = TransToken.ui('Are you sure you want to delete "{palette}"?')
TRANS_BUILTIN = TransToken.ui('Builtin / Readonly')  # i18n: Palette group title.


class PaletteUI:
    """UI for selecting palettes."""
    def __init__(
        self, f: ttk.Frame, menu: tk.Menu,
        *,
        tk_img: TKImages,
        cmd_clear: Callable[[], None],
        cmd_shuffle: Callable[[], None],
        get_items: Callable[[], ItemPos],
        set_items: Callable[[Palette], Awaitable[None]],
    ) -> None:
        """Initialises the palette pane.

        The parameters are used to communicate with the item list:
        - cmd_clear and cmd_shuffle are called to do those actions to the list.
        - get_items is called to retrieve the current list of selected items.
        - save_btn_state is the .state() method on the save button.
        - set_items is called to apply a palette to the list of items.
        """
        self.palettes: dict[UUID, Palette] = {
            pal.uuid: pal
            for pal in paletteLoader.load_palettes()
        }
        prev_state = config.APP.get_cur_conf(PaletteState, default=PaletteState())
        self.selected_uuid = prev_state.selected
        self.hidden_defaults = set(prev_state.hidden_defaults)
        self.var_save_settings = tk.BooleanVar(value=prev_state.save_settings)
        self.var_pal_select = tk.StringVar(value=self.selected_uuid.hex)
        self.get_items = get_items
        self.set_items = set_items

        f.rowconfigure(2, weight=1)
        f.columnconfigure(0, weight=1)

        btn_bar = ttk.Frame(f)
        btn_bar.grid(row=0, column=0, columnspan=2, sticky='EW', padx=5)
        btn_bar.columnconfigure(0, weight=1)
        btn_bar.columnconfigure(1, weight=1)
        btn_bar.columnconfigure(2, weight=1)

        self.ui_btn_save = localisation.set_text(
            ttk.Button(btn_bar, command=self.event_save),
            TransToken.ui("Save"),
        )
        self.ui_btn_save.grid(row=0, column=0, sticky="EW")

        localisation.set_text(
            ttk.Button(btn_bar, command=self.event_save_as),
            TransToken.ui("Save As"),
        ).grid(row=0, column=1, sticky="EW")

        self.ui_remove = localisation.set_text(
            ttk.Button(btn_bar, command=self.event_remove),
            TransToken.ui("Delete"),
        )
        self.ui_remove.grid(row=0, column=2, sticky="EW")

        self.ui_treeview = treeview = ttk.Treeview(f, show='tree', selectmode='browse')
        self.ui_treeview.grid(row=2, column=0, sticky="NSEW")
        # We need to delay this a frame, so the selection completes.
        self.ui_treeview.tag_bind(
            TREE_TAG_PALETTES, '<ButtonPress>',
            lambda e: background_run(self.event_select_tree),
        )

        check_save_settings = ttk.Checkbutton(
            f,
            variable=self.var_save_settings,
            command=self._store_configuration,
        )
        localisation.set_text(check_save_settings, TransToken.ui('Save Settings in Palettes'))
        check_save_settings.grid(row=3, column=0, sticky="EW", padx=5)

        self.tk_img = tk_img

        # Avoid re-registering the double-lambda, just do it here.
        # This makes clicking the groups return selection to the palette.
        evtid_reselect = self.ui_treeview.register(self.treeview_reselect)
        self.ui_treeview.tag_bind(
            TREE_TAG_GROUPS, '<ButtonPress>',
            lambda e: treeview.tk.call('after', 'idle', evtid_reselect),
        )

        # And ensure when focus returns we reselect, in case it deselects.
        f.winfo_toplevel().bind('<FocusIn>', lambda e: self.treeview_reselect(), add=True)

        scrollbar = tk_tools.HidingScroll(
            f,
            orient='vertical',
            command=self.ui_treeview.yview,
        )
        scrollbar.grid(row=2, column=1, sticky="NS")
        self.ui_treeview['yscrollcommand'] = scrollbar.set

        if tk_tools.USE_SIZEGRIP:
            ttk.Sizegrip(f).grid(row=3, column=1)

        self.ui_menu = menu
        self.ui_group_menus: dict[str, tk.Menu] = {}
        self.ui_group_treeids: dict[str, str] = {}

        menu.add_command(command=self.event_save, accelerator=tk_tools.ACCEL_SAVE)
        localisation.set_menu_text(menu, TransToken.ui('Save Palette'))
        self.ui_readonly_indexes = [menu.index('end')]

        menu.add_command(command=self.event_save_as, accelerator=tk_tools.ACCEL_SAVE_AS,)
        localisation.set_menu_text(menu, TransToken.ui('Save Palette As...'))

        menu.add_command(
            label='Delete Palette',  # This name is overwritten later
            command=self.event_remove,
        )
        self.ui_menu_delete_index = menu.index('end')

        menu.add_command(command=self.event_change_group)
        localisation.set_menu_text(menu, TransToken.ui('Change Palette Group...'))
        self.ui_readonly_indexes.append(menu.index('end'))

        menu.add_command(command=self.event_rename)
        localisation.set_menu_text(menu, TransToken.ui('Rename Palette...'))
        self.ui_readonly_indexes.append(menu.index('end'))

        menu.add_separator()

        menu.add_checkbutton(variable=self.var_save_settings)
        localisation.set_menu_text(menu, TransToken.ui('Save Settings in Palettes'))

        menu.add_separator()

        menu.add_command(command=cmd_clear)
        localisation.set_menu_text(menu, TransToken.ui('Clear'))

        menu.add_command(command=cmd_shuffle)
        localisation.set_menu_text(menu, TransToken.ui('Fill Palette'))

        menu.add_separator()

        self.ui_menu_palettes_index = menu.index('end') + 1
        localisation.add_callback(call=True)(self.update_state)

    @property
    def selected(self) -> Palette:
        """Retrieve the currently selected palette."""
        try:
            return self.palettes[self.selected_uuid]
        except KeyError:
            LOGGER.warning('No such palette with ID {}', self.selected_uuid)
            return self.palettes[UUID_PORTAL2]

    def update_state(self) -> None:
        """Update the UI to show correct state."""
        # This is called if languages change, so we can just immediately convert translation tokens.

        # Clear out all the current data.
        for grp_menu in self.ui_group_menus.values():
            grp_menu.delete(0, 'end')
        self.ui_menu.delete(self.ui_menu_palettes_index, 'end')

        # Detach all groups + children, and get a list of existing ones.
        existing: set[str] = set()
        for group_id in self.ui_group_treeids.values():
            existing.update(self.ui_treeview.get_children(group_id))
            self.ui_treeview.detach(group_id)
        for pal_id in self.ui_treeview.get_children(''):
            if pal_id.startswith('pal_'):
                self.ui_treeview.delete(pal_id)

        groups: dict[str, list[Palette]] = {}
        for pal in self.palettes.values():
            if pal is self.selected or pal.uuid not in self.hidden_defaults:
                groups.setdefault(pal.group, []).append(pal)

        for group, palettes in sorted(groups.items(), key=lambda t: (t[0] != paletteLoader.GROUP_BUILTIN, t[0])):
            if group == paletteLoader.GROUP_BUILTIN:
                group = str(TRANS_BUILTIN)
            if group:
                try:
                    grp_menu = self.ui_group_menus[group]
                except KeyError:
                    grp_menu = self.ui_group_menus[group] = tk.Menu(self.ui_menu)
                self.ui_menu.add_cascade(label=group, menu=grp_menu)

                try:
                    grp_tree = self.ui_group_treeids[group]
                except KeyError:
                    grp_tree = self.ui_group_treeids[group] = self.ui_treeview.insert(
                        '', 'end',
                        text=group,
                        open=True,
                        tags=TREE_TAG_GROUPS,
                    )
                else:
                    self.ui_treeview.move(grp_tree, '', 9999)
            else:  # '', directly add.
                grp_menu = self.ui_menu
                grp_tree = ''  # Root.
            for pal in sorted(palettes, key=lambda p: str(p.name)):
                gear_img: tkImg | str = self.tk_img.sync_load(ICO_GEAR) if pal.settings is not None else ''
                grp_menu.add_radiobutton(
                    label=str(pal.name),
                    value=pal.uuid.hex,
                    # If we remake the palette menus inside this event handler, it tries
                    # to select the old menu item (likely), so a crash occurs. Delay until
                    # another frame.
                    command=lambda: background_run(self.event_select_menu),
                    variable=self.var_pal_select,
                    image=gear_img,
                    compound='left',
                )
                pal_id = 'pal_' + pal.uuid.hex
                if pal_id in existing:
                    existing.remove(pal_id)
                    self.ui_treeview.move(pal_id, grp_tree, 99999)
                    self.ui_treeview.item(
                        pal_id,
                        text=str(pal.name),
                        image=gear_img,
                    )
                else:  # New
                    self.ui_treeview.insert(
                        grp_tree, 'end',
                        text=str(pal.name),
                        iid='pal_' + pal.uuid.hex,
                        image=gear_img,
                        tags=TREE_TAG_PALETTES,
                    )
        # Finally, strip any ones which were removed.
        if existing:
            self.ui_treeview.delete(*existing)

        # Select the currently selected UUID.
        self.ui_treeview.selection_set('pal_' + self.selected.uuid.hex)
        self.ui_treeview.see('pal_' + self.selected.uuid.hex)

        if self.selected.readonly:
            self.ui_menu.entryconfigure(
                self.ui_menu_delete_index,
                label=TRANS_HIDE_NAMED.format(name=self.selected.name),
            )
            localisation.set_text(self.ui_remove, TRANS_HIDE)

            self.ui_btn_save.state(('disabled',))
            for ind in self.ui_readonly_indexes:
                self.ui_menu.entryconfigure(ind, state='disabled')
        else:
            self.ui_menu.entryconfigure(
                self.ui_menu_delete_index,
                label=TRANS_DELETE_NAMED.format(name=self.selected.name),
            )
            localisation.set_text(self.ui_remove, TRANS_DELETE)

            self.ui_btn_save.state(('!disabled',))
            for ind in self.ui_readonly_indexes:
                self.ui_menu.entryconfigure(ind, state='normal')

        if self.selected.uuid in PALETTE_FORCE_SHOWN:
            self.ui_remove.state(('disabled',))
            self.ui_menu.entryconfigure(self.ui_menu_delete_index, state='disabled')
        else:
            self.ui_remove.state(('!disabled',))
            self.ui_menu.entryconfigure(self.ui_menu_delete_index, state='normal')

    def _store_configuration(self) -> None:
        """Save the state of the palette to the config."""
        config.APP.store_conf(PaletteState(
            self.selected_uuid,
            self.var_save_settings.get(),
            frozenset(self.hidden_defaults),
        ))

    def reset_hidden_palettes(self) -> None:
        """Clear all hidden palettes, and save."""
        self.hidden_defaults.clear()
        self._store_configuration()
        self.update_state()

    def event_remove(self) -> None:
        """Remove the currently selected palette."""
        pal = self.selected
        if pal.readonly:
            if pal.uuid in PALETTE_FORCE_SHOWN:
                return  # Disallowed.
            self.hidden_defaults.add(pal.uuid)
        elif tk_tools.askyesno(
            title=TransToken.ui('BEE2 - Delete Palette'),
            message=TRANS_SHOULD_DELETE.format(palette=pal.name),
            parent=TK_ROOT,
        ):
            pal.delete_from_disk()
            del self.palettes[pal.uuid]
        self.select_palette(UUID_PORTAL2)
        self.update_state()
        background_run(self.set_items, self.selected)

    def event_save(self) -> None:
        """Save the current palette over the original name."""
        if self.selected.readonly:
            self.event_save_as()
            return
        else:
            self.selected.items = self.get_items()
            if self.var_save_settings.get():
                self.selected.settings = config.get_pal_conf()
            else:
                self.selected.settings = None
            self.selected.save(ignore_readonly=True)
        self.update_state()

    def event_save_as(self) -> None:
        """Save the palette with a new name."""
        name = tk_tools.prompt(TransToken.ui("BEE2 - Save Palette"), TransToken.ui("Enter a name:"))
        if name is None:
            # Cancelled...
            return
        pal = Palette(name, self.get_items())
        while pal.uuid in self.palettes:  # Should be impossible.
            pal.uuid = paletteLoader.uuid4()

        if self.var_save_settings.get():
            pal.settings = config.get_pal_conf()

        pal.save()
        self.palettes[pal.uuid] = pal
        self.select_palette(pal.uuid)
        self.update_state()

    def event_rename(self) -> None:
        """Rename an existing palette."""
        if self.selected.readonly:
            return
        name = tk_tools.prompt(TransToken.ui("BEE2 - Save Palette"), TransToken.ui("Enter a name:"))
        if name is None:
            # Cancelled...
            return
        self.selected.name = TransToken.untranslated(name)
        self.update_state()

    def select_palette(self, uuid: UUID) -> None:
        """Select a new palette. This does not update items/settings!"""
        if uuid in self.palettes:
            self.selected_uuid = uuid
            self._store_configuration()
        else:
            LOGGER.warning('Unknown UUID {}!', uuid.hex)

    def event_change_group(self) -> None:
        """Change the group of a palette."""
        if self.selected.readonly:
            return
        res = tk_tools.prompt(
            TransToken.ui("BEE2 - Change Palette Group"),
            TransToken.ui('Enter the name of the group for this palette, or "" to ungroup.'),
            validator=lambda x: x,
        )
        if res is not None:
            self.selected.group = res.strip('<>')
            self.selected.save()
            self.update_state()

    async def event_select_menu(self) -> None:
        """Called when the menu buttons are clicked."""
        uuid_hex = self.var_pal_select.get()
        self.select_palette(UUID(hex=uuid_hex))
        await self.set_items(self.selected)
        self.update_state()

    async def event_select_tree(self) -> None:
        """Called when palettes are selected on the treeview."""
        try:
            uuid_hex = self.ui_treeview.selection()[0][4:]
        except IndexError:  # No selection, exit.
            return
        self.var_pal_select.set(uuid_hex)
        self.select_palette(UUID(hex=uuid_hex))
        await self.set_items(self.selected)
        self.update_state()

    def treeview_reselect(self) -> None:
        """When a group item is selected on the tree, reselect the palette."""
        # This could be called before all the items are added to the UI.
        uuid_hex = 'pal_' + self.selected.uuid.hex
        if self.ui_treeview.exists(uuid_hex):
            self.ui_treeview.selection_set(uuid_hex)
