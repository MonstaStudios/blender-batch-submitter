"""
Blend Parser - Fast binary parsing of .blend files using blender_asset_tracer.

Reads .blend files directly without launching Blender, extracting metadata like:
- Scene names, frame ranges, output paths
- View layers, render engines, resolutions
- Active scene detection
- Blender version

Adapted from B-renderon 4.0's obtencion_info_blend.py
"""
import os
from pathlib import Path
from typing import Optional, Dict, List, Any

try:
    from blender_asset_tracer import blendfile
    from blender_asset_tracer.blendfile import iterators
    BLENDER_ASSET_TRACER_AVAILABLE = True
except ImportError:
    BLENDER_ASSET_TRACER_AVAILABLE = False


# Blender image format enum → string mapping
# From Blender source: source/blender/makesdna/DNA_scene_types.h
IMAGE_FORMAT_MAP = {
    1: 'IRIS',
    2: 'JPEG',
    3: 'MOVIE',
    4: 'IRIZ',
    7: 'RAWTGA',
    14: 'PNG',
    15: 'BMP',
    17: 'TIFF',
    18: 'OPEN_EXR',
    19: 'FFMPEG',
    20: 'FRAMESERVER',
    21: 'CINEON',
    22: 'OPEN_EXR_MULTILAYER',
    23: 'DPX',
    24: 'DDS',
    25: 'JP2',
    26: 'WEBP',
    28: 'AVI_JPEG',
}


def is_available() -> bool:
    """Check if blender_asset_tracer library is installed."""
    return BLENDER_ASSET_TRACER_AVAILABLE


def get_name(block) -> str:
    """Extract name from a Blender ID block (id_name field).
    
    ID blocks store names as: 2-char type code + null-terminated name.
    Example: b'SCScene' → 'Scene'
    """
    try:
        # Try getting the id.name field directly
        id_name = block.get((b'id', b'name'))
        if id_name and isinstance(id_name, bytes):
            # Skip first 2 bytes (ID type code like 'SC', 'OB', etc.)
            name_bytes = id_name[2:]
            # Decode and strip null terminators
            name = name_bytes.decode('utf-8', errors='ignore').rstrip('\x00')
            if name:
                return name
    except Exception:
        pass
    return "Unknown"


def get_active_scene_name(blend) -> Optional[str]:
    """Detect active scene from Window Manager block.
    
    The Window Manager stores references to the active window and scene.
    """
    try:
        wm_blocks = blend.find_blocks_from_code(b'WM')
        if not wm_blocks:
            return None
        
        wm = wm_blocks[0]
        # Get first window from linked list
        window = wm.get_pointer((b'windows', b'first'))
        if window:
            # Get scene pointer from window
            scene_ptr = window.get_pointer(b'scene')
            if scene_ptr:
                return get_name(scene_ptr)
    except Exception:
        pass
    return None


def resolve_output_path(path_str: str, blend_dir: Path) -> str:
    """Resolve Blender's relative output paths (//) to absolute paths.
    
    Blender uses '//' prefix for paths relative to the .blend file.
    Example: '//render/output_####' → '/full/path/to/blend/render/output_####'
    """
    if not path_str:
        return path_str
    
    if path_str.startswith('//'):
        # Strip '//' and join with blend file directory
        relative_path = path_str[2:]
        return str(blend_dir / relative_path)
    
    return path_str


def get_view_layers(scene_block) -> List[str]:
    """Extract all view layers from a scene block.
    
    View layers are stored as a linked list in scene.view_layers.
    Walk the list using iterators.listbase().
    """
    layers = []
    seen = set()  # Track seen names to avoid duplicates
    try:
        # Get first view layer from linked list
        first_layer = scene_block.get_pointer((b'view_layers', b'first'))
        if first_layer:
            # Walk the linked list
            for layer in iterators.listbase(first_layer):
                # ViewLayer has name field directly (not id.name like ID blocks)
                name_raw = layer.get(b'name', b'')
                if isinstance(name_raw, bytes):
                    layer_name = name_raw.decode('utf-8', errors='ignore').rstrip('\x00')
                    if layer_name and layer_name not in seen:
                        layers.append(layer_name)
                        seen.add(layer_name)
    except Exception:
        # Silently fail - some files may not have view layers
        pass
    
    return layers


def parse_scene(scene_block, blend_dir: Path) -> Dict[str, Any]:
    """Extract metadata from a single scene block."""
    scene_data = {}
    
    try:
        # Scene name
        scene_data['name'] = get_name(scene_block)
        
        # Frame range (stored in RenderData struct: scene.r)
        scene_data['frame_start'] = scene_block.get((b'r', b'sfra'), 1)
        scene_data['frame_end'] = scene_block.get((b'r', b'efra'), 250)
        scene_data['frame_step'] = scene_block.get((b'r', b'frame_step'), 1)
        
        # Output path (scene.r.pic - char array)
        output_raw = scene_block.get((b'r', b'pic'), b'')
        if isinstance(output_raw, bytes):
            output_raw = output_raw.decode('utf-8', errors='ignore').rstrip('\x00')
        scene_data['output_path'] = resolve_output_path(output_raw, blend_dir)
        
        # View layers
        scene_data['view_layers'] = get_view_layers(scene_block)
        
        # Render engine - scene.r.engine is char[32] in RenderData struct
        # Note: RenderData 'r' is an embedded struct in Scene, not a pointer
        engine_raw = scene_block.get((b'r', b'engine'), b'')
        
        # Decode engine string
        if engine_raw and isinstance(engine_raw, bytes):
            engine_str = engine_raw.decode('utf-8', errors='ignore').rstrip('\x00')
            scene_data['render_engine'] = engine_str if engine_str else 'BLENDER_EEVEE'
        else:
            scene_data['render_engine'] = 'BLENDER_EEVEE'
        
        # Compositing enabled (scene.use_nodes)
        use_nodes = scene_block.get(b'use_nodes', 0)
        scene_data['use_nodes'] = bool(use_nodes)
        
        # Resolution
        scene_data['resolution_x'] = scene_block.get((b'r', b'xsch'), 1920)
        scene_data['resolution_y'] = scene_block.get((b'r', b'ysch'), 1080)
        
        # Output format (scene.r.im_format.imtype - enum stored as int)
        format_int = scene_block.get((b'r', b'im_format', b'imtype'), 14)  # Default: PNG
        scene_data['output_format'] = IMAGE_FORMAT_MAP.get(format_int, f'UNKNOWN_{format_int}')
        
    except Exception as e:
        scene_data['parse_error'] = str(e)
    
    return scene_data


def parse_blend(filepath: str) -> Dict[str, Any]:
    """Parse a .blend file and extract metadata.
    
    Returns dict matching the format from blend_inspector.py:
    {
        "file": str,
        "job_name": str,
        "scenes": [{"name": ..., "frame_start": ..., ...}],
        "active_scene": str (optional),
        "blender_version": str (optional)
    }
    
    Raises:
        ImportError: If blender_asset_tracer is not installed
        Exception: If file cannot be parsed
    """
    if not BLENDER_ASSET_TRACER_AVAILABLE:
        raise ImportError("blender_asset_tracer library is not installed. "
                         "Install with: pip install blender-asset-tracer")
    
    filepath_abs = Path(filepath).resolve()
    blend_dir = filepath_abs.parent
    
    # Open .blend file (uses caching for efficiency)
    blend = blendfile.open_cached(filepath_abs)
    
    # Detect Blender version from file header
    blender_version = None
    try:
        version_int = blend.header.version
        # Version stored as int: 420 = 4.2.0, 271 = 2.71
        major = version_int // 100
        minor = version_int % 100
        blender_version = f"{major}.{minor}"
    except Exception:
        pass
    
    # Find active scene
    active_scene = get_active_scene_name(blend)
    
    # Find all scene blocks (type code 'SC')
    scene_blocks = blend.find_blocks_from_code(b'SC')
    
    scenes = []
    for scene_block in scene_blocks:
        scene_data = parse_scene(scene_block, blend_dir)
        scenes.append(scene_data)
    
    # Build metadata dict matching blend_inspector.py format
    metadata = {
        "file": str(filepath_abs),
        "job_name": filepath_abs.stem.replace(".blend", ""),
        "scenes": scenes,
    }
    
    if active_scene:
        metadata["active_scene"] = active_scene
    
    if blender_version:
        metadata["blender_version"] = blender_version
    
    return metadata


if __name__ == "__main__":
    # CLI test interface
    import sys
    import json
    import traceback
    
    if len(sys.argv) < 2:
        print("Usage: python blend_parser.py <file.blend>")
        sys.exit(1)
    
    try:
        metadata = parse_blend(sys.argv[1])
        print(json.dumps(metadata, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
