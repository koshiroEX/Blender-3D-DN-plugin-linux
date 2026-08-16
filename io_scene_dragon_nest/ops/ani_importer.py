import bpy
import os

from mathutils import Quaternion, Vector, Matrix
from typing import List

from .common import oriented_matrix, translation_matrix, rotation_matrix, scale_matrix, get_armature_matrices
from ..gui import gui
from ..types.ani import ANI, ANIM, AnimationBone

ANIM_ID_ALL = -1


def set_bone_keyframes(arm_obj, bone_name, data_type, frame, values):
    """Inserts keyframes directly into the bone pose and sets the interpolation to LINEAR."""
    pose_bone = arm_obj.pose.bones.get(bone_name) or arm_obj.pose.bones.get(bone_name[:-2])
    if not pose_bone:
        return

    setattr(pose_bone, data_type, values)
    pose_bone.keyframe_insert(data_path=data_type, frame=frame)


def set_linear_interpolation(action):
    """Ensures that all F-Curves generated in the Action use LINEAR interpolation."""
    fcurves = []

    # Support for the new Slots/Bindings structure in Blender 5.x
    if hasattr(action, "slots"):
        for slot in action.slots:
            if hasattr(slot, "fcurves"):
                fcurves.extend(list(slot.fcurves))
            elif hasattr(slot, "curves"):
                fcurves.extend(list(slot.curves))
    if hasattr(action, "bindings"):
        for binding in action.bindings:
            if hasattr(binding, "fcurves"):
                fcurves.extend(list(binding.fcurves))
    if hasattr(action, "fcurves"):
        fcurves.extend(list(action.fcurves))
    if hasattr(action, "curves"):
        fcurves.extend(list(action.curves))

    for fc in fcurves:
        for kf in fc.keyframe_points:
            kf.interpolation = 'LINEAR'


def find_last_keyframe_time(action):
    last_frame = 0
    fcurves = []

    if hasattr(action, "slots"):
        for slot in action.slots:
            if hasattr(slot, "fcurves"):
                fcurves.extend(list(slot.fcurves))
            elif hasattr(slot, "curves"):
                fcurves.extend(list(slot.curves))
    if hasattr(action, "bindings"):
        for binding in action.bindings:
            if hasattr(binding, "fcurves"):
                fcurves.extend(list(binding.fcurves))
    if hasattr(action, "fcurves"):
        fcurves.extend(list(action.fcurves))
    if hasattr(action, "curves"):
        fcurves.extend(list(action.curves))

    for fc in fcurves:
        for kf in fc.keyframe_points:
            if kf.co[0] > last_frame:
                last_frame = kf.co[0]
    return int(last_frame)


def get_active_armature(context):
    arm_obj = context.view_layer.objects.active
    if arm_obj and type(arm_obj.data) == bpy.types.Armature:
        return arm_obj


def local_to_basis_matrix(local_matrix, global_matrix, parent_matrix):
    return global_matrix.inverted() @ (parent_matrix @ local_matrix)


def connect_armature_bones(context, armature, animation_bones: List[AnimationBone]) -> bool:
    bpy.ops.object.mode_set(mode='EDIT')

    for ani_bone in animation_bones:
        arm_bone = armature.edit_bones.get(ani_bone.name) or armature.edit_bones.get(ani_bone.name[:-2])
        if not arm_bone:
            context.window_manager.popup_menu(gui.invalid_armature, title='Error', icon='ERROR')
            bpy.ops.object.mode_set(mode='OBJECT')
            return False

        arm_bone.parent = armature.edit_bones.get(ani_bone.parent_name)

    bpy.ops.object.mode_set(mode='OBJECT')
    return True


def create_actions(armature_object, animation_bones: List[AnimationBone], anim_id=ANIM_ID_ALL):
    def_matrices = get_armature_matrices(armature_object)
    actions = {}

    anim_data = armature_object.animation_data or armature_object.animation_data_create()

    for ani_bone in animation_bones:
        bone = armature_object.data.bones.get(ani_bone.name) or armature_object.data.bones.get(ani_bone.name[:-2])
        if not bone:
            continue

        def_mat = def_matrices[bone.name]
        parent_def_mat = def_matrices[bone.parent.name] if bone.parent else Matrix()

        for act_idx, anim in enumerate(ani_bone.animations):
            if anim_id not in (ANIM_ID_ALL, act_idx):
                continue

            act = actions.get(act_idx)
            if not act:
                act = bpy.data.actions.new("dn_animation %d" % act_idx)
                actions[act_idx] = act

            # Assign the Action directly to the armature so the native API registers the keyframes in the correct Action.
            anim_data.action = act

            loc = Vector(anim.base_location.unpack())
            rot = Quaternion((
                anim.base_rotation.w,
                anim.base_rotation.x,
                anim.base_rotation.y,
                anim.base_rotation.z,
            ))
            scl = Vector(anim.base_scale.unpack())

            mat = oriented_matrix(translation_matrix(loc) @ rotation_matrix(rot) @ scale_matrix(scl))
            mat_basis = local_to_basis_matrix(mat, def_mat, parent_def_mat)

            set_bone_keyframes(armature_object, bone.name, "location", 0, mat_basis.to_translation())
            set_bone_keyframes(armature_object, bone.name, "rotation_quaternion", 0, mat_basis.to_quaternion())
            set_bone_keyframes(armature_object, bone.name, "scale", 0, mat_basis.to_scale())

            for kf in anim.locations:
                mat = translation_matrix((kf.value.x, kf.value.z, kf.value.y))
                mat_basis = local_to_basis_matrix(mat, def_mat, parent_def_mat)
                set_bone_keyframes(armature_object, bone.name, "location", kf.frame, mat_basis.to_translation())

            for kf in anim.rotations:
                rot = Quaternion((kf.value.w, kf.value.x, kf.value.y, kf.value.z))
                mat = oriented_matrix(rotation_matrix(rot))
                mat_basis = local_to_basis_matrix(mat, def_mat, parent_def_mat)
                set_bone_keyframes(armature_object, bone.name, "rotation_quaternion", kf.frame, mat_basis.to_quaternion())

            for kf in anim.scales:
                mat = scale_matrix((kf.value.x, kf.value.z, kf.value.y))
                mat_basis = local_to_basis_matrix(mat, def_mat, parent_def_mat)
                set_bone_keyframes(armature_object, bone.name, "scale", kf.frame, mat_basis.to_scale())

            set_linear_interpolation(act)

    return actions


class AniImporter:

    def import_data(self, context, options):
        anim_id = options.get("animation_id")
        if anim_id is None:
            anim_id = ANIM_ID_ALL
        elif anim_id != ANIM_ID_ALL:
            anim_id = max(0, min(anim_id, len(self.ani.names) - 1))

        arm_obj = get_active_armature(context)
        if not arm_obj:
            return

        arm = arm_obj.data
        if not connect_armature_bones(context, arm, self.ani.bones):
            return

        actions = create_actions(arm_obj, self.ani.bones, anim_id)
        for act_idx in sorted(actions):
            act = actions[act_idx]
            act.name = self.ani.names[act_idx]
            self.actions.append(act)

        animation_data = arm_obj.animation_data or arm_obj.animation_data_create()
        animation_data.action = self.actions[-1]

        context.scene.frame_start = 0
        context.scene.frame_end = find_last_keyframe_time(animation_data.action)

        self.imported = True

    def load_file(self, context, filename) -> bool:
        self.ani = ANI()
        self.ani.load_file(filename)

        if not self.ani.file_type.startswith("Eternity Engine Ani File"):
            context.window_manager.popup_menu(gui.invalid_ani_type, title='Error', icon='ERROR')
            return False

        return True

    def __init__(self):
        self.ani = None
        self.actions = []
        self.imported = False


class AnimImporter:

    def import_data(self, context, options):
        arm_obj = get_active_armature(context)
        if not arm_obj:
            return

        arm = arm_obj.data
        if not connect_armature_bones(context, arm, self.anim.bones):
            return

        actions = create_actions(arm_obj, self.anim.bones)
        self.action = actions[0]

        animation_data = arm_obj.animation_data or arm_obj.animation_data_create()
        animation_data.action = self.action

        context.scene.frame_start = 0
        context.scene.frame_end = find_last_keyframe_time(animation_data.action)

        self.imported = True

    def load_file(self, context, filename) -> bool:
        self.anim = ANIM()
        self.anim.load_file(filename)
        return True

    def __init__(self):
        self.anim = None
        self.action = None
        self.imported = False


def load(context, filepath):
    arm_obj = get_active_armature(context)
    if not arm_obj:
        return None

    if filepath.lower().endswith(".ani"):
        importer = AniImporter()
        if not importer.load_file(context, filepath):
            return None

    if filepath.lower().endswith(".anim"):
        importer = AnimImporter()
        if not importer.load_file(context, filepath):
            return None

        importer.import_data(context, {})
        if not importer.action:
            return None

        importer.action.name = os.path.basename(filepath)[:-5]

    return importer
