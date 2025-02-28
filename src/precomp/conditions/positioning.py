"""Various conditions related to the position/orientation of items."""
import math
from typing import Iterable, Tuple, Dict, Set, Callable

from srctools.vmf import EntityGroup

import utils
from precomp.conditions import (
    make_flag, make_result, resolve_offset,
    DIRECTIONS,
)
from precomp import tiling, brushLoc
from srctools import Vec, Angle, Matrix, conv_float, Property, Entity
from srctools.logger import get_logger


COND_MOD_NAME = 'Positioning'
LOGGER = get_logger(__name__, alias='cond.positioning')

# Predicates for tiles.
# We optimise to a lookup table.
TILE_PREDICATES: Dict[str, Set[tiling.TileType]] = {}


@make_flag(
    'rotation',
    'angle',
    'angles',
    'orient',
    'orientation',
    'dir',
    'direction',
)
def flag_angles(flag: Property) -> Callable[[Entity], bool]:
    """Check that a instance is pointed in a direction.

    The value should be either just the angle to check, or a block of
    options:

    - `direction`: A unit vector (XYZ value) pointing in a direction, or some
        keywords: `+z`, `-y`, `N`/`S`/`E`/`W`, `up`/`down`, `floor`/`ceiling`, or `walls` for any wall side.
    - `From_dir`: The direction the unrotated instance is pointed in.
        This lets the flag check multiple directions.
    - `Allow_inverse`: If true, this also returns True if the instance is
        pointed the opposite direction .
    """

    if flag.has_children():
        targ_angle = flag['direction', '0 0 0']
        from_dir_str = flag['from_dir', '0 0 1']
        if from_dir_str.casefold() in DIRECTIONS:
            from_dir = Vec(DIRECTIONS[from_dir_str.casefold()])
        else:
            from_dir = Vec.from_str(from_dir_str, 0, 0, 1)
        allow_inverse = flag.bool('allow_inverse')
    else:
        targ_angle = flag.value
        from_dir = Vec(0, 0, 1)
        allow_inverse = False

    try:
        normal = DIRECTIONS[targ_angle.casefold()]
    except KeyError:
        normal = Vec.from_str(targ_angle)

    def check_orient(inst: Entity) -> bool:
        """Check the orientation against the instance."""
        inst_normal = from_dir @ Matrix.from_angstr(inst['angles'])

        if normal == 'WALL':
            # Special case - it's not on the floor or ceiling
            return abs(inst_normal.z) < 1e-6
        else:
            return inst_normal == normal or (
                allow_inverse and -inst_normal == normal
            )
    return check_orient


def brush_at_loc(
    inst: Entity,
    props: Property,
) -> Tuple[tiling.TileType, bool, Set[tiling.TileType]]:
    """Common code for posIsSolid and ReadSurfType.

    This returns the average tiletype, if both colors were found,
    and a set of all types found.
    """
    origin = Vec.from_str(inst['origin'])
    orient = Matrix.from_angstr(inst['angles'])

    # Allow using pos1 instead, to match pos2.
    pos = props.vec('pos1' if 'pos1' in props else 'pos')
    pos.z -= 64  # Subtract so origin is the floor-position

    pos.localise(origin, orient)

    norm: Vec = round(props.vec('dir', 0, 0, 1) @ orient, 6)

    if props.bool('gridpos') and norm is not None:
        for axis in 'xyz':
            # Don't realign things in the normal's axis -
            # those are already fine.
            if norm[axis] == 0:
                pos[axis] = pos[axis] // 128 * 128 + 64

    result_var = props['setVar', '']
    # RemoveBrush is the pre-tiling name.
    should_remove = props.bool('RemoveTile', props.bool('RemoveBrush', False))

    tile_types: Set[tiling.TileType] = set()
    both_colors = False

    # Place info_targets to mark where we're checking.
    # These are hidden in a visgroup.
    if utils.DEV_MODE:
        try:
            [visgroup] = [vis for vis in inst.map.vis_tree if vis.name == 'TileAtLoc']
        except ValueError:
            visgroup = inst.map.create_visgroup('TileAtLoc')
        first_trace = inst.map.create_ent('info_target', origin=pos, targetname=inst['targetname'])
        first_trace.vis_shown = False
        first_trace.hidden = True
        first_trace.visgroup_ids.add(visgroup.id)
    else:
        visgroup = first_trace = None

    if 'pos2' in props:
        pos2 = props.vec('pos2')
        pos2.z -= 64  # Subtract so origin is the floor-position
        pos2.localise(origin, orient)

        if visgroup is not None and first_trace is not None:
            # Place a second for the bounding box, grouped with the first.
            second_trace = inst.map.create_ent('info_target', origin=pos2, targetname=inst['targetname'])
            second_trace.vis_shown = False
            second_trace.hidden = True
            second_trace.visgroup_ids.add(visgroup.id)
            group = EntityGroup(inst.map)
            inst.map.groups[group.id] = group
            first_trace.groups.add(group.id)
            second_trace.groups.add(group.id)

        bbox_min, bbox_max = Vec.bbox(round(pos, 6), round(pos2, 6))

        white_count = black_count = 0

        for pos in Vec.iter_grid(bbox_min, bbox_max, 32):
            try:
                tiledef, u, v = tiling.find_tile(pos, norm)
            except KeyError:
                continue

            tile_type = tiledef[u, v]
            tile_types.add(tile_type)
            if should_remove:
                tiledef[u, v] = tiling.TileType.VOID
            if tile_type.is_tile:
                if tile_type.color is tiling.Portalable.WHITE:
                    white_count += 1
                else:
                    black_count += 1

        both_colors = white_count > 0 and black_count > 0

        if white_count == black_count == 0:
            tile_type = tiling.TileType.VOID
            tile_types.add(tiling.TileType.VOID)
        elif white_count > black_count:
            tile_type = tiling.TileType.WHITE
        else:
            tile_type = tiling.TileType.BLACK
    else:
        # Single tile.
        pos2 = pos
        try:
            tiledef, u, v = tiling.find_tile(pos, norm)
        except KeyError:
            tile_type = tiling.TileType.VOID
        else:
            tile_type = tiledef[u, v]
            if should_remove:
                tiledef[u, v] = tiling.TileType.VOID
        tile_types.add(tile_type)

    LOGGER.debug('PosIsSolid check {} - {} @ {} = {}', pos, pos2, norm, tile_types)
    if first_trace is not None:
        first_trace.comments = 'Tiles: ' + ' '.join([t.name for t in tile_types])

    if result_var:
        if tile_type.is_tile:
            # Don't distinguish between 4x4, goo sides
            inst.fixup[result_var] = tile_type.color.value
        elif tile_type is tiling.TileType.VOID:
            inst.fixup[result_var] = 'none'
        else:
            inst.fixup[result_var] = tile_type.name.casefold()

    return tile_type, both_colors, tile_types


@make_flag('posIsSolid')
def flag_brush_at_loc(inst: Entity, flag: Property):
    """Checks to see if a tile is present at the given location.

    - `Pos` is the position of the tile, where `0 0 0` is the floor-position
       of the brush.
    - `Dir` is the normal the face is pointing. `(0 0 1)` is up.
    - `Pos2`: If set, checks all the tiles in a bounding box.
    - `Type` defines the type the brush must be:
        - `Any` requires some sort of surface.
        - `Tile` allows a black/white tile of some kind.
        - `None` means that no surface must be present.
        - `White` requires a portalable surface.
        - `Black` requires a non-portalable surface.
        - `4x4` requires a tile that forces the 4x4 size.
        - `1x1` requires a tile that does not force a size.
    - `Mode` specifies how the check is done, if `Pos2` is provided:
        - `avg`/`average` (default): The tiles will be averaged together.
        if there is an equal number of white/black tiles, black wins.
        - `and`: The check must pass on all tiles.
        - `or`: The check must pass on any tile.
        - `same`: Checks to see if all tiles are the same. `Type` is ignored.
        - `diff`/`different`: Checks to see if there are multiple tile types.
          `Type` is ignored.
    - `SetVar` defines an instvar which will be given a value of `black`,
      `white` or `none` depending on the average colour of tiles.
    - If `gridPos` is true, the position will be snapped, so it aligns with
      the 128 grid (Useful with fizzler/light strip items).
    - `RemoveTile`: If set to `1`, the tile will be removed if found.
    """
    avg_type, both_colors, tile_types = brush_at_loc(inst, flag)

    if 'pos2' not in flag:  # Others are useless.
        mode = 'avg'
    else:
        mode = flag['mode', 'avg'].casefold()

    if mode in ('same', 'diff', 'different'):
        # These don't need 'type', force the value to ensure it can't error out.
        des_type = 'any'
    else:
        des_type = flag['type', 'any'].casefold()

    if des_type in ('same', 'diff', 'different'):
        LOGGER.warning(
            'Using type={} in posIsSolid is deprecated, put this in mode!',
            des_type,
        )
        # Modify the properties, so it doesn't happen again.
        mode = flag['mode'] = des_type
        des_type = 'any'
        del flag['type']

    try:
        tile_pred = TILE_PREDICATES[des_type]
    except KeyError:
        LOGGER.warning(
            'Unknown tile type "{}" for posIsSolid command!',
            des_type
        )
        return False

    if mode in ('diff', 'different'):
        return both_colors
    elif mode == 'same':
        return not both_colors and all(tile.is_tile for tile in tile_types)
    elif mode == 'and':
        return all(tile in tile_pred for tile in tile_types)
    elif mode == 'or':
        return any(tile in tile_pred for tile in tile_types)
    elif mode == 'avg':
        return avg_type in tile_pred

    LOGGER.warning(
        'Unknown check mode "{}" for posIsSolid command!'
    )
    return False


def _fill_predicates() -> None:
    """Set TILE_PREDICATES."""
    WHITE = tiling.Portalable.WHITE
    BLACK = tiling.Portalable.BLACK
    TILE_4x4 = tiling.TileSize.TILE_4x4
    TILE_1x1 = tiling.TileSize.TILE_1x1

    def pred_any(tile: tiling.TileType) -> bool:
        """Any kind of surface."""
        return tile is not tiling.TileType.VOID

    def pred_none(tile: tiling.TileType) -> bool:
        """No surface present."""
        return tile is tiling.TileType.VOID

    def pred_tile(tile: tiling.TileType) -> bool:
        """A solid black/white tile."""
        return tile.is_tile

    def pred_white(tile: tiling.TileType) -> bool:
        """A portal surface."""
        return tile.is_tile and tile.color is WHITE

    def pred_black(tile: tiling.TileType) -> bool:
        """A non-portal surface."""
        return tile.is_tile and tile.color is BLACK

    def pred_1x1(tile: tiling.TileType) -> bool:
        """A tile which produces an unrestricted surface."""
        return tile.is_tile and tile.tile_size is TILE_1x1

    def pred_4x4(tile: tiling.TileType) -> bool:
        """A tile that forces a 4x4 size."""
        return tile.is_tile and tile.tile_size is TILE_4x4

    for name, func in list(locals().items()):
        if name.startswith('pred_'):
            # Collapse it down into a lookup table.
            TILE_PREDICATES[name[5:]] = set(filter(
                func, tiling.TileType.__members__.values()
            ))


_fill_predicates()
del _fill_predicates


@make_result('ReadSurfType')
def res_brush_at_loc(inst: Entity, res: Property):
    """Read the type of surface at a particular location.

    - `Pos` is the position of the tile, where `0 0 0` is the floor-position
       of the brush.
    - `Dir` is the normal the face is pointing. `(0 0 1)` is up.
    - `Pos2`: If set, causes the check to average the tiles in a bounding box.
      If no tiles are present they're treated as a lack of them.
      Otherwise, the dominant colour wins, with ties treated as black.
    - `ResultVar` is the variable which is set. This will be set to
      `black`, `white` or `none` depending on the average colour of tiles.
    - If `gridPos` is true, the position will be snapped so it aligns with
      the 128 grid (Useful with fizzler/light strip items).
    - `RemoveTile`: If set to `1`, the tile will be removed if found.
    """
    # Alias PosIsSolid to also be a result, for using the variable mode by itself.
    res['setVar'] = res['resultVar']
    brush_at_loc(inst, res)


@make_flag('PosIsGoo')
def flag_goo_at_loc(inst: Entity, flag: Property):
    """Check to see if a given location is submerged in goo.

    `0 0 0` is the origin of the instance, values are in `128` increments.
    """
    return brushLoc.POS.lookup_world(resolve_offset(inst, flag.value, scale=128)).is_goo


@make_flag('BlockType')
def flag_blockpos_type(inst: Entity, flag: Property):
    """Determine the type of a grid position.

    If the value is single value, that should be the type.
    Otherwise, the value should be a block with 'offset' and 'type' values.
    The offset is in block increments, with 0 0 0 equal to the mounting voxel.
    If 'offset2' is also provided, all positions in the bounding box will
    be checked.

    The type should be a space-seperated list of locations:

    * `VOID` (Outside the map)
    * `SOLID` (Full wall cube)
    * `EMBED` (Hollow wall cube)
    * `AIR` (Inside the map, may be occupied by items)
    * `OCCUPIED` (Known to be occupied by items)
    * `PIT` (Bottomless pits, any)
        * `PIT_SINGLE` (one-high)
        * `PIT_TOP`
        * `PIT_MID`
        * `PIT_BOTTOM`
    * `GOO`
        * `GOO_SINGLE` (one-deep goo)
        * `GOO_TOP` (goo surface)
        * `GOO_MID`
        * `GOO_BOTTOM` (floor)
    """
    pos2 = None

    if flag.has_children():
        pos1 = resolve_offset(inst, flag['offset', '0 0 0'], scale=128, zoff=-128)
        types = flag['type'].split()
        if 'offset2' in flag:
            pos2 = resolve_offset(inst, flag['offset2', '0 0 0'], scale=128, zoff=-128)
    else:
        types = flag.value.split()
        pos1 = Vec()

    bbox: Iterable[Vec]
    if pos2 is not None:
        bbox = Vec.iter_grid(*Vec.bbox(pos1, pos2), stride=128)
    else:
        bbox = [pos1]

    for pos in bbox:
        block = brushLoc.POS.lookup_world(pos)
        for block_type in types:
            try:
                allowed = brushLoc.BLOCK_LOOKUP[block_type.casefold()]
            except KeyError:
                raise ValueError('"{}" is not a valid block type!'.format(block_type))
            if block in allowed:
                break  # To next position
        else:
            return False  # Didn't match any in this list.
    return True  # Matched all positions.


@make_result('SetBlock')
def res_set_block(inst: Entity, res: Property) -> None:
    """Set a block to the given value, overwriting the existing value.

    - `type` is the type of block to set:
        * `VOID` (Outside the map)
        * `SOLID` (Full wall cube)
        * `EMBED` (Hollow wall cube)
        * `AIR` (Inside the map, may be occupied by items)
        * `OCCUPIED` (Known to be occupied by items)
        * `PIT_SINGLE` (one-high)
        * `PIT_TOP`
        * `PIT_MID`
        * `PIT_BOTTOM`
        * `GOO_SINGLE` (one-deep goo)
        * `GOO_TOP` (goo surface)
        * `GOO_MID`
        * `GOO_BOTTOM` (floor)
    - `offset` is in block increments, with `0 0 0` equal to the mounting surface.
    - If 'offset2' is also provided, all positions in the bounding box will be set.
    """
    try:
        new_vals = brushLoc.BLOCK_LOOKUP[res['type'].casefold()]
    except KeyError:
        raise ValueError('"{}" is not a valid block type!'.format(res['type']))

    try:
        [new_val] = new_vals
    except ValueError:
        # TODO: This could spread top/mid/bottom through the bbox...
        raise ValueError(
            f'Can\'t use compound block type "{res["type"]}", specify '
            "_SINGLE/TOP/MID/BOTTOM"
        )

    pos1 = resolve_offset(inst, res['offset', '0 0 0'], scale=128, zoff=-128)

    if 'offset2' in res:
        pos2 = resolve_offset(inst, res['offset2', '0 0 0'], scale=128, zoff=-128)
        for pos in Vec.iter_grid(*Vec.bbox(pos1, pos2), stride=128):
            brushLoc.POS.set_world(pos, new_val)
    else:
        brushLoc.POS.set_world(pos1, new_val)


@make_result('forceUpright')
def res_force_upright(inst: Entity):
    """Position an instance to orient upwards while keeping the normal.

    The result angle will have pitch and roll set to 0. Vertical
    instances are unaffected.
    """
    normal = Vec(0, 0, 1) @ Angle.from_str(inst['angles'])
    if abs(normal.z) > 0.1:
        return
    ang = math.degrees(math.atan2(normal.y, normal.x))
    inst['angles'] = '0 {:g} 0'.format(ang % 360)  # Don't use negatives


@make_result('switchOrientation')
def res_alt_orientation(res: Property) -> Callable[[Entity], None]:
    """Apply an alternate orientation.

    "wall" makes the attaching surface in the -X direction, making obs rooms,
    corridors etc easier to build. The Z axis points in the former +X direction.
    "ceiling" flips the instance, making items such as droppers easier to build.
    The X axis remains unchanged.
    """
    val = res.value.casefold()
    if val == 'wall':
        pose = Matrix.from_angle(-90, 180, 0)
    elif val in ('ceil', 'ceiling'):
        pose = Matrix.from_roll(180)
    else:
        raise ValueError(f'Unknown orientation type "{res.value}"!')

    def swap_orient(inst: Entity) -> None:
        """Apply the new orientation."""
        inst['angles'] = pose @ Matrix.from_angstr(inst['angles'])
    return swap_orient


@make_result('setAngles')
def res_set_angles(inst: Entity, res: Property):
    """Set the orientation of an instance to a certain angle."""
    inst['angles'] = inst.fixup.substitute(res.value)


@make_result('OffsetInst', 'offsetinstance')
def res_translate_inst(inst: Entity, res: Property):
    """Translate the instance locally by the given amount.

    The special values `<piston>`, `<piston_bottom>` and `<piston_top>` can be
    used to offset it based on the starting position, bottom or top position
    of a piston platform.
    """
    inst['origin'] = resolve_offset(inst, res.value)


@make_result('OppositeWallDist')
def res_calc_opposite_wall_dist(inst: Entity, res: Property):
    """Calculate the distance between this item and the opposing wall.

    The value is stored in the `$var` specified by the property value.
    Alternately it is set by `ResultVar`, and `offset` adds or subtracts to the value.
    `GooCollide` means that it will stop when goo is found, otherwise it is
    ignored.
    `GooAdjust` means additionally if the space is GOO, the distance will
    be modified so that it specifies the surface of the goo.
    """
    if res.has_children():
        result_var = res['ResultVar']
        dist_off = res.float('offset')
        collide_goo = res.bool('GooCollide')
        adjust_goo = res.bool('GooAdjust')
    else:
        result_var = res.value
        dist_off = 0
        collide_goo = adjust_goo = False

    origin = Vec.from_str(inst['origin'])
    normal = Matrix.from_angstr(inst['angles']).up()

    mask = [
        brushLoc.Block.SOLID,
        brushLoc.Block.EMBED,
        brushLoc.Block.PIT_BOTTOM,
        brushLoc.Block.PIT_SINGLE,
    ]

    # Only if actually downward.
    if normal.z < -0.9 and collide_goo:
        mask.append(brushLoc.Block.GOO_TOP)
        mask.append(brushLoc.Block.GOO_SINGLE)

    opposing_pos = brushLoc.POS.raycast_world(
        origin,
        normal,
        mask,
    )

    if adjust_goo and brushLoc.POS.lookup_world(opposing_pos + 128 * normal).is_goo:
        # If the top is goo, adjust so the 64 below is the top of the goo.
        dist_off += 32

    inst.fixup[result_var] = (origin - opposing_pos).mag() + dist_off


@make_result('RotateInst', 'RotateInstance')
def res_rotate_inst(inst: Entity, res: Property) -> None:
    """Rotate the instance around an axis.

    If `axis` is specified, it should be a normal vector and the instance will
    be rotated `angle` degrees around it.
    Otherwise, `angle` is a pitch-yaw-roll angle which is applied.
    `around` can be a point (local, pre-rotation) which is used as the origin.

    Tip: If you want to match angled panels, rotate with an axis of `0 -1 0`
    and an around value of `0 -64 -64`.
    """
    angles = Matrix.from_angstr(inst['angles'])
    if 'axis' in res:
        orient = Matrix.axis_angle(
            Vec.from_str(inst.fixup.substitute(res['axis'])),
            conv_float(inst.fixup.substitute(res['angle'])),
        )
    else:
        orient = Matrix.from_angstr(inst.fixup.substitute(res['angle']))

    try:
        offset = Vec.from_str(inst.fixup.substitute(res['around']))
    except LookupError:
        pass
    else:
        origin = Vec.from_str(inst['origin'])
        inst['origin'] = origin + (-offset @ orient + offset) @ angles

    inst['angles'] = (orient @ angles).to_angle()
