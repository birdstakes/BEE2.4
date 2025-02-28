"""Handles user errors found, displaying a friendly interface to the user."""
from __future__ import annotations

from pathlib import Path
from typing_extensions import Final, Literal
from typing import Iterable, Mapping, Tuple
import os.path
import pickle

from srctools import Vec, VMF, AtomicWriter, logger
import attrs

from user_errors import DATA_LOC, UserError, TOK_VBSP_LEAK
from precomp.tiling import TileDef, TileType
from precomp.barriers import BarrierType
from precomp.brushLoc import Grid
from precomp import options
import consts


__all__ = ['UserError', 'TOK_VBSP_LEAK', 'load_tiledefs']

LOGGER = logger.get_logger(__name__)
NORM_2_ORIENT: Final[Mapping[
    Tuple[float, float, float],
    Literal['u', 'd', 'n', 's', 'e', 'w']
]] = {
    (0.0, 0.0, +1.0): 'u',
    (0.0, 0.0, -1.0): 'd',
    (0.0, +1.0, 0.0): 'n',
    (0.0, -1.0, 0.0): 's',
    (+1.0, 0.0, 0.0): 'e',
    (-1.0, 0.0, 0.0): 'w',
}


def load_tiledefs(tiles: Iterable[TileDef], grid: Grid) -> None:
    """Load tiledef info into a simplified tiles list."""
    # noinspection PyProtectedMember
    simple_tiles = UserError._simple_tiles

    tiles_white = simple_tiles["white"] = []
    tiles_black = simple_tiles["black"] = []
    tiles_goo_partial = simple_tiles["goopartial"] = []
    tiles_goo_full = simple_tiles["goofull"] = []
    for tile in tiles:
        if not tile.base_type.is_tile:
            continue
        block_type = grid['world': (tile.pos + 128 * tile.normal)]
        if not block_type.inside_map:
            continue
        # Tint the area underneath goo, by just using two textures with the appropriate tints.
        if tile.base_type is TileType.GOO_SIDE:
            if block_type.is_top and tile.normal.z < 0.9:
                tile_list = tiles_goo_partial
            else:
                tile_list = tiles_goo_full
        elif tile.base_type.is_white:
            tile_list = tiles_white
        else:
            tile_list = tiles_black
        tile_list.append({
            'orient': NORM_2_ORIENT[tile.normal.as_tuple()],
            'position': tuple((tile.pos + 64 * tile.normal) / 128),
        })
    goo_tiles = simple_tiles["goo"] = []
    for pos, block in grid.items():
        if block.is_top:  # Both goo and bottomless pits.
            goo_tiles.append({
                'orient': 'd',
                'position': tuple((pos + (0.5, 0.5, 0.75)).as_tuple()),
            })


def load_barriers(barriers: dict[
    tuple[tuple[float, float, float], tuple[float, float, float]],
    BarrierType,
]) -> None:
    """Load barrier data for display in errors."""
    # noinspection PyProtectedMember
    glass_list = UserError._simple_tiles["glass"] = []
    # noinspection PyProtectedMember
    grate_list = UserError._simple_tiles["grating"] = []
    kind_to_list = {
        BarrierType.GLASS: glass_list,
        BarrierType.GRATING: grate_list,
    }
    for (pos_tup, normal_tup), kind in barriers.items():
        pos = Vec(pos_tup) + 56.0 * Vec(normal_tup)
        kind_to_list[kind].append({
            'orient': NORM_2_ORIENT[normal_tup],
            'position': tuple((pos / 128.0).as_tuple()),
        })


def make_map(error: UserError) -> VMF:
    """Generate a map which triggers the error each time.

    This map is as simple as possible to make compile time quick.
    The content loc is the location of the web resources.
    """
    lang_filename = options.get(str, 'error_translations')
    if lang_filename and (lang_path := Path(lang_filename)).is_file():
        info = attrs.evolve(error.info, language_file=lang_path)
    else:
        info = error.info
    with AtomicWriter(DATA_LOC, is_bytes=True) as f:
        pickle.dump(info, f, pickle.HIGHEST_PROTOCOL)

    LOGGER.info('Localisation file: {!r}', lang_filename)

    vmf = VMF()
    vmf.map_ver = 1
    vmf.spawn['skyname'] = 'sky_black_nofog'
    vmf.spawn['detailmaterial'] = "detail/detailsprites"
    vmf.spawn['detailvbsp'] = "detail.vbsp"
    vmf.spawn['maxblobcount'] = "250"
    vmf.spawn['paintinmap'] = "0"

    vmf.add_brushes(vmf.make_hollow(
        Vec(),
        Vec(128, 128, 128),
        thick=32,
        mat=consts.Tools.NODRAW,
        inner_mat=consts.Tools.BLACK,
    ))
    # Ensure we have at least one lightmapped surface,
    # so VRAD computes lights.
    roof_detail = vmf.make_prism(
        Vec(48, 48, 120),
        Vec(80, 80, 124)
    )
    roof_detail.top.mat = consts.BlackPan.BLACK_FLOOR
    roof_detail.top.scale = 64
    vmf.create_ent('func_detail').solids.append(roof_detail.solid)

    # VScript displays the webpage, then kicks you back to the editor
    # if the map is swapped back to. VRAD detects the classname and adds the script.
    vmf.create_ent(
        'bee2_user_error',
        origin="64 64 1",
        angles="0 0 0",
    )
    # We need a light, so the map compiles lights and doesn't turn on mat_fullbright.
    vmf.create_ent(
        'light',
        origin="64 64 64",
        angles="0 0 0",
        spawnflags="0",
        _light="255 255 255 1",
        _lightHDR="-1 -1 -1 -1",
        _lightscaleHDR="1",
        _constant_attn="0",
        _quadratic_attn="1",
        _linear_attn="1",
    )
    # Needed to get a default cubemap to be generated.
    vmf.create_ent('env_cubemap', origin='64 64 64')
    # Put two coop spawns in there too.
    vmf.create_ent(
        'info_coop_spawn',
        origin="64 32 1",
        angles="0 0 0",
        forcegunonspawn=0,
        targetname='supress_orange_portalgun_spawn',  # Stop guns
        startingteam=2,
        enabled=1,
    )
    vmf.create_ent(
        'info_coop_spawn',
        origin="64 96 1",
        angles="0 0 0",
        forcegunonspawn=0,
        targetname='supress_blue_portalgun_spawn',
        startingteam=3,
        enabled=1,
    )
    # Suppress portalgun spawn, pinging, taunts
    for state in [
        'portalgun_nospawn',
        'no_pinging_blue', 'no_pinging_orange',
        'no_taunting_blue', 'no_taunting_orange',
    ]:
        vmf.create_ent(
            'env_global',
            origin='64 64 32',
            globalstate=state,
            initialstate=1,
            counter=0,
            spawnflags=1,  # Set initial state
        )
    return vmf
