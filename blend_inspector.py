"""
Blend Inspector - Extract metadata from .blend files via Blender headless.

Usage: blender -b scene.blend -P blend_inspector.py

Outputs a JSON line prefixed with BLEND_INSPECTOR_JSON: to stdout.
The prefix allows reliable parsing since Blender prints its own startup messages.
"""
import bpy
import json
import sys

metadata = {
    "file": bpy.data.filepath,
    "job_name": bpy.path.basename(bpy.data.filepath).replace(".blend", ""),
    "scenes": [],
}

for scene in bpy.data.scenes:
    scene_data = {
        "name": scene.name,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "frame_step": scene.frame_step,
        "render_engine": scene.render.engine,
        "resolution_x": scene.render.resolution_x,
        "resolution_y": scene.render.resolution_y,
        "output_path": scene.render.filepath,
        "output_format": scene.render.image_settings.file_format,
        "view_layers": [vl.name for vl in scene.view_layers if vl.use],
        "use_nodes": scene.use_nodes,
    }
    metadata["scenes"].append(scene_data)

print("BLEND_INSPECTOR_JSON:" + json.dumps(metadata))
sys.exit(0)
