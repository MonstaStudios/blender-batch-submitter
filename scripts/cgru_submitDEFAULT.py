import bpy
import os
import sys

# Default settings - will be overridden by user custom settings
scene = bpy.context.scene
for scene in bpy.data.scenes:
    if hasattr(scene, 'cycles'):
        scene.cycles.tile_size = 8192
    try:
        # Example default settings (can be modified)
        scene.render.use_persistent_data = True
        scene.render.use_border = True
        scene.render.compositor_device = 'GPU'
        scene.cycles.device = 'GPU'
        scene.cycles.preview_samples = 16

    except Exception as e:
        print(f"Warning: Could not apply default settings: {e}")

# CUSTOM USER SETTINGS WILL BE INJECTED HERE
# {{CUSTOM_SETTINGS}}



# Submit to CGRU
bpy.ops.wm.save_mainfile(compress=True, relative_remap=True)
if hasattr(scene, 'cgru') and scene.cgru:
    bpy.ops.cgru.submit()
    print("✓ Submitted to CGRU")
else:
    print("⚠ CGRU not available in this scene")