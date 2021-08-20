"""Items dealing with antlines - Antline Corners and Antlasers."""
from __future__ import annotations
from enum import Enum
from typing import Dict, List, Tuple, Set, FrozenSet, Callable, Union

from precomp import instanceLocs, connections, conditions
import srctools.logger
from precomp.conditions import make_result
from srctools import VMF, Property, Output, Vec, Entity, Angle


COND_MOD_NAME = None

LOGGER = srctools.logger.get_logger(__name__, alias='cond.antlines')

AntlineConn = connections.Config(
    '<Antline>',
    input_type=connections.InputType.OR,
    output_act=(None, 'OnUser2'),
    output_deact=(None, 'OnUser1'),
)

NAME_SPR: Callable[[str, int], str] = '{}-fx_sp_{}'.format
NAME_BEAM_LOW: Callable[[str, int], str] = '{}-fx_b_low_{}'.format
NAME_BEAM_CONN: Callable[[str, int], str] = '{}-fx_b_conn_{}'.format
NAME_CABLE: Callable[[str, int], str] = '{}-cab_{}'.format


class NodeType(Enum):
    """Handle our two types of item."""
    CORNER = 'corner'
    LASER = 'laser'


class RopeState(Enum):
    """Used to link up ropes."""
    NONE = 'none'  # No rope here.
    UNLINKED = 'unlinked'  # Rope ent, with no target.
    LINKED = 'linked'  # Rope ent, with target already.

    @staticmethod
    def from_node(
        points: Dict[connections.Item, Union[Entity, str]],
        node: connections.Item,
    ) -> Tuple['RopeState', Union[Entity, str]]:
        """Compute the state and ent/name from the points data."""
        try:
            ent = points[node]
        except KeyError:
            return RopeState.NONE, ''
        if isinstance(ent, str):
            return RopeState.LINKED, ent
        else:
            return RopeState.UNLINKED, ent


class Group:
    """Represents a group of markers."""
    def __init__(self, start: connections.Item, typ: NodeType):
        self.type = typ  # Antlaser or corner?
        self.nodes: List[connections.Item] = [start]
        # We use a frozenset here to ensure we don't double-up the links -
        # users might accidentally do that.
        self.links: Set[FrozenSet[connections.Item]] = set()
        # Create the item for the entire group of markers.
        logic_ent = start.inst.map.create_ent(
            'info_target',
            origin=start.inst['origin'],
            targetname=start.name,
        )
        self.item = connections.Item(
            logic_ent,
            AntlineConn,
            start.ant_floor_style,
            start.ant_wall_style,
        )
        connections.ITEMS[self.item.name] = self.item


def on_floor(node: connections.Item) -> bool:
    """Check if this node is on the floor."""
    norm = Vec(z=1) @ Angle.from_str(node.inst['angles'])
    return norm.z > 0.9


@make_result('AntLaser')
def res_antlaser(vmf: VMF, res: Property):
    """The condition to generate AntLasers and Antline Corners.

    This is executed once to modify all instances.
    """
    conf_inst_corner = instanceLocs.resolve('<item_bee2_antline_corner>', silent=True)
    conf_inst_laser = instanceLocs.resolve(res['instance'])
    conf_glow_height = Vec(z=res.float('GlowHeight', 48) - 64)
    conf_las_start = Vec(z=res.float('LasStart') - 64)
    conf_rope_off = res.vec('RopePos')
    conf_toggle_targ = res['toggleTarg', '']

    beam_conf = res.find_key('BeamKeys', or_blank=True)
    glow_conf = res.find_key('GlowKeys', or_blank=True)
    cable_conf = res.find_key('CableKeys', or_blank=True)

    if beam_conf:
        # Grab a copy of the beam spawnflags so we can set our own options.
        conf_beam_flags = beam_conf.int('spawnflags')
        # Mask out certain flags.
        conf_beam_flags &= (
            0
            | 1  # Start On
            | 2  # Toggle
            | 4  # Random Strike
            | 8  # Ring
            | 16  # StartSparks
            | 32  # EndSparks
            | 64  # Decal End
            #| 128  # Shade Start
            #| 256  # Shade End
            #| 512  # Taper Out
        )
    else:
        conf_beam_flags = 0

    conf_outputs = [
        Output.parse(prop)
        for prop in res
        if prop.name in ('onenabled', 'ondisabled')
    ]

    # Find all the markers.
    nodes: dict[str, connections.Item] = {}
    node_type: dict[str, NodeType] = {}

    for inst in vmf.by_class['func_instance']:
        filename = inst['file'].casefold()
        name = inst['targetname']
        if filename in conf_inst_laser:
            node_type[name] = NodeType.LASER
        elif filename in conf_inst_corner:
            node_type[name] = NodeType.CORNER
        else:
            continue

        try:
            # Remove the item - it's no longer going to exist after
            # we're done.
            nodes[name] = connections.ITEMS.pop(name)
        except KeyError:
            raise ValueError('No item for "{}"?'.format(name)) from None

    if not nodes:
        # None at all.
        return conditions.RES_EXHAUSTED

    # Now find every connected group, recording inputs, outputs and links.
    todo = set(nodes.values())

    groups: list[Group] = []

    # Node -> is grouped already.
    node_pairing = dict.fromkeys(nodes.values(), False)

    while todo:
        start = todo.pop()
        # Synthesise the Item used for logic.
        # We use a random info_target to manage the IO data.
        group = Group(start, node_type[start.name])
        groups.append(group)
        for node in group.nodes:
            # If this node has no non-node outputs, destroy the antlines.
            has_output = False
            node_pairing[node] = True

            for conn in list(node.outputs):
                neighbour = conn.to_item
                todo.discard(neighbour)
                pair_state = node_pairing.get(neighbour, None)
                if pair_state is None or group.type is not node_type[neighbour.name]:
                    # Not a node or different item type, it must therefore
                    # be a target of our logic.
                    conn.from_item = group.item
                    has_output = True
                    continue
                elif pair_state is False:
                    # Another node.
                    group.nodes.append(neighbour)
                # else: True, node already added.

                # For nodes, connect link.
                conn.remove()
                group.links.add(frozenset({node, neighbour}))

            # If we have a real output, we need to transfer it.
            # Otherwise we can just destroy it.
            if has_output:
                node.transfer_antlines(group.item)
            else:
                node.delete_antlines()

            # Do the same for inputs, so we can catch that.
            for conn in list(node.inputs):
                neighbour = conn.from_item
                todo.discard(neighbour)
                pair_state = node_pairing.get(neighbour, None)
                if pair_state or group.type is not node_type[neighbour.name]:
                    # Not a node or different item type, it must therefore
                    # be a target of our logic.
                    conn.to_item = group.item
                    continue
                elif pair_state is False:
                    # Another node.
                    group.nodes.append(neighbour)
                # else: True, node already added.

                # For nodes, connect link.
                conn.remove()
                group.links.add(frozenset({neighbour, node}))

    # Now every node is in a group. Generate the actual entities.
    for group in groups:
        # We generate two ent types. For each marker, we add a sprite
        # and a beam pointing at it. Then for each connection
        # another beam.

        # Choose a random item name to use for our group.
        base_name = group.nodes[0].name

        out_enable = [Output('', '', 'FireUser2')]
        out_disable = [Output('', '', 'FireUser1')]
        for output in conf_outputs:
            if output.output.casefold() == 'onenabled':
                out_enable.append(output.copy())
            else:
                out_disable.append(output.copy())

        group.item.enable_cmd = tuple(out_enable)
        group.item.disable_cmd = tuple(out_disable)

        if group.type is NodeType.LASER and conf_toggle_targ:
            # Make the group info_target into a texturetoggle.
            toggle = group.item.inst
            toggle['classname'] = 'env_texturetoggle'
            toggle['target'] = conditions.local_name(group.nodes[0].inst, conf_toggle_targ)

        # Node -> index for targetnames.
        indexes: Dict[connections.Item, int] = {}

        # For cables, it's a bit trickier than the beams.
        # The cable ent itself is the one which decides what it links to,
        # so we need to potentially make endpoint cables at locations with
        # only "incoming" lines.
        # So this dict is either a targetname to indicate cables with an
        # outgoing connection, or the entity for endpoints without an outgoing
        # connection.
        cable_points: Dict[connections.Item, Union[Entity, str]] = {}

        for i, node in enumerate(group.nodes, start=1):
            indexes[node] = i
            node.name = base_name

            sprite_pos = conf_glow_height.copy()
            sprite_pos.localise(
                Vec.from_str(node.inst['origin']),
                Angle.from_str(node.inst['angles']),
            )

            if glow_conf:
                # First add the sprite at the right height.
                sprite = vmf.create_ent('env_sprite')
                for prop in glow_conf:
                    sprite[prop.name] = conditions.resolve_value(node.inst, prop.value)

                sprite['origin'] = sprite_pos
                sprite['targetname'] = NAME_SPR(base_name, i)
            elif beam_conf:
                # If beams but not sprites, we need a target.
                vmf.create_ent(
                    'info_target',
                    origin=sprite_pos,
                    targetname=NAME_SPR(base_name, i),
                )

            if beam_conf:
                # Now the beam going from below up to the sprite.
                beam_pos = conf_las_start.copy()
                beam_pos.localise(
                    Vec.from_str(node.inst['origin']),
                    Angle.from_str(node.inst['angles']),
                )
                beam = vmf.create_ent('env_beam')
                for prop in beam_conf:
                    beam[prop.name] = conditions.resolve_value(node.inst, prop.value)

                beam['origin'] = beam['targetpoint'] = beam_pos
                beam['targetname'] = NAME_BEAM_LOW(base_name, i)
                beam['LightningStart'] = beam['targetname']
                beam['LightningEnd'] = NAME_SPR(base_name, i)
                beam['spawnflags'] = conf_beam_flags | 128  # Shade Start

        if beam_conf:
            for i, (node_a, node_b) in enumerate(group.links):
                beam = vmf.create_ent('env_beam')
                conditions.set_ent_keys(beam, node_a.inst, res, 'BeamKeys')
                beam['origin'] = beam['targetpoint'] = node_a.inst['origin']
                beam['targetname'] = NAME_BEAM_CONN(base_name, i)
                beam['LightningStart'] = NAME_SPR(base_name, indexes[node_a])
                beam['LightningEnd'] = NAME_SPR(base_name, indexes[node_b])
                beam['spawnflags'] = conf_beam_flags

        if cable_conf:
            build_cables(
                vmf,
                group,
                cable_points,
                base_name,
                beam_conf,
                conf_rope_off,
            )

    return conditions.RES_EXHAUSTED


def build_cables(
    vmf: VMF,
    group: Group,
    cable_points: dict[connections.Item, Union[Entity, str]],
    base_name: str,
    beam_conf: Property,
    conf_rope_off: Vec,
) -> None:
    """Place Old-Aperture style cabling."""
    # We have a couple different situations to deal with here.
    # Either end could Not exist, be Unlinked, or be Linked = 8 combos.
    # Always flip so we do A to B.
    # AB |
    # NN | Make 2 new ones, one is an endpoint.
    # NU | Flip, do UN.
    # NL | Make A, link A to B. Both are linked.
    # UN | Make B, link A to B. B is unlinked.
    # UU | Link A to B, A is now linked, B is unlinked.
    # UL | Link A to B. Both are linked.
    # LN | Flip, do NL.
    # LU | Flip, do UL
    # LL | Make A, link A to B. Both are linked.
    rope_ind = 0  # Uniqueness value.
    for node_a, node_b in group.links:
        state_a, ent_a = RopeState.from_node(cable_points, node_a)
        state_b, ent_b = RopeState.from_node(cable_points, node_b)

        if (state_a is RopeState.LINKED
            or (state_a is RopeState.NONE and
                state_b is RopeState.UNLINKED)
        ):
            # Flip these, handle the opposite order.
            state_a, state_b = state_b, state_a
            ent_a, ent_b = ent_b, ent_a
            node_a, node_b = node_b, node_a

        pos_a = conf_rope_off.copy()
        pos_a.localise(
            Vec.from_str(node_a.inst['origin']),
            Angle.from_str(node_a.inst['angles']),
        )

        pos_b = conf_rope_off.copy()
        pos_b.localise(
            Vec.from_str(node_b.inst['origin']),
            Angle.from_str(node_b.inst['angles']),
        )

        # Need to make the A rope if we don't have one that's unlinked.
        if state_a is not RopeState.UNLINKED:
            rope_a = vmf.create_ent('move_rope')
            for prop in beam_conf:
                rope_a[prop.name] = node_a.inst.fixup.substitute(node_a.inst, prop.value)
            rope_a['origin'] = pos_a
            rope_ind += 1
            rope_a['targetname'] = NAME_CABLE(base_name, rope_ind)
        else:
            # It is unlinked, so it's the rope to use.
            rope_a = ent_a

        # Only need to make the B rope if it doesn't have one.
        if state_b is RopeState.NONE:
            rope_b = vmf.create_ent('move_rope')
            for prop in beam_conf:
                rope_b[prop.name] = node_b.inst.fixup.substitute(prop.value)
            rope_b['origin'] = pos_b
            rope_ind += 1
            name_b = rope_b['targetname'] = NAME_CABLE(base_name, rope_ind)

            cable_points[node_b] = rope_b  # Someone can use this.
        elif state_b is RopeState.UNLINKED:
            # Both must be unlinked, we aren't using this link though.
            name_b = ent_b['targetname']
        else:  # Linked, we just have the name.
            name_b = ent_b

        # By here, rope_a should be an unlinked rope,
        # and name_b should be a name to link to.
        rope_a['nextkey'] = name_b

        # Figure out how much slack to give.
        # If on floor, we need to be taut to have clearance.
        if on_floor(node_a) or on_floor(node_b):
            rope_a['slack'] = 60
        else:
            rope_a['slack'] = 125

        # We're always linking A to B, so A is always linked!
        if state_a is not RopeState.LINKED:
            cable_points[node_a] = rope_a['targetname']
