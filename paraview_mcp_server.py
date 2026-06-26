"""
ParaView MCP Server

This script runs as a standalone process and:
1. Connects to ParaView using its Python API over network
2. Exposes key ParaView functionality through the MCP protocol
3. Updates visualizations in the existing ParaView viewport

Usage:
1. Start pvserver with --multi-clients flag (e.g., pvserver --multi-clients --server-port=11111)
2. Start ParaView app and connect to the server
3. Configure Claude Desktop to use this script

"""
import os
import sys
import logging
import argparse
import base64
import json
import time
import functools
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image
from paraview_manager import ParaViewManager

# Configure logging
log_dir = Path.home() / "paraview_logs"
os.makedirs(log_dir, exist_ok=True)
log_file = log_dir / "paraview_mcp_external.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, mode="w"),
        logging.StreamHandler()
    ]
)

# Default prompt that instructs LLMs how to interact with ParaView
default_prompt = """
When using ParaView through this interface, please follow these guidelines:

1. IMPORTANT: Only call strictly necessary ParaView functions per reply (and please limit the total number of call per reply). This ensures operations execute in a more interative manner and no excessive calls to related but non-essential functions. 

2. The only execute multiple repeated function call when given a target goal (e.g., identify a specific object), where different parameters need to used (e.g., isosurface with different isovalue). Avoid repeated calling of color map function unless user specific ask for color map design.

3. Paraview will be connect to mcp server on starup so no need to connect first.


"""
    
logger = logging.getLogger("pv_external_mcp")

def log_startup_banner():
    logger.info("-" * 60)
    logger.info("Paraview MCP Server Started")
    logger.info(f"  Started at : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"  Log File   : {log_file}")
    logger.info("")
    logger.info("Log Line Prefixes:")
    logger.info("   [CALL] tool invoked, shows tool name and all arguments")
    logger.info("   [DONE] tool finished, shows elapsed time and output size")
    logger.info("   [LOAD] a dataset was loaded into Paraview")
    logger.info("   [ERROR] exception caught in a tool or manager method")
    logger.info("-" * 60)

log_startup_banner()

def timed_tool(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        arg_summary = ", ".join([repr(a) for a in args] + [f"{k}={repr(v)}" for k, v in kwargs.items()])
        logger.info(f"[CALL] {func.__name__}({arg_summary})")
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        logger.info(f"[DONE] {func.__name__} | {elapsed:.3f}s")
        return result
    return wrapper

# Create the ParaView manager
pv_manager = ParaViewManager()

# Initialize FastMCP server
mcp = FastMCP("ParaView")

# ----------------------------------------------------------------------------
# MCP Tools for ParaView
# ----------------------------------------------------------------------------

@mcp.tool()
@timed_tool
def load_data(file_path: str) -> str:
    """
    Load a data file into ParaView and register it as a source in the pipeline. Supports VTK, VTU, VTS, EXODUS, CSV, RAW binary volumes, and other formats ParaView natively reads. For RAW files, dimensions and scalar type are auto-detected from the filename (e.g. foot_256x256x256_uint8.raw).

    Args:
        file_path: Absolute path to the file to load.
    
    Returns: Status message.
    """
    success, message, _, source_name = pv_manager.load_data(file_path)
    if success:
        return f"{message}. Source registered as '{source_name}'."
    else:
        return message

@mcp.tool()
@timed_tool
def save_contour_as_stl(stl_filename: str = "contour.stl") -> str:
    """
    Export the currently active source as an STL triangle mesh file.
    The file is written to the same directory as the originally loaded data.
    Works on any source that produces a surface — isosurfaces, slices, extracted surfaces, or any geometry with triangle cells. Volumetric sources (vtkImageData) cannot be directly exported as STL; apply a Contour or ExtractSurface filter first.

    Args:
        stl_filename: Output filename including .stl extension (default: 'contour.stl').
        
    Returns: Full path of the saved file on success, or error description.
    """
    success, message, path = pv_manager.save_contour_as_stl(stl_filename)
    return message

@mcp.tool()
@timed_tool
def create_source(source_type: str) -> str:
    """
    Create a new geometric source and add it to the pipeline. These are synthetic objects with no associated scalar data — useful for
    reference geometry, clipping planes, or testing visualization settings.

    Args:
        source_type: One of Sphere, Cone, Cylinder, Plane, Box.
        
    Returns: Confirmation message with the registered source name, or error description.
    """
    success, message, _, source_name = pv_manager.create_source(source_type)
    if success:
        return f"{message}. Source registered as '{source_name}'."
    else:
        return message

@mcp.tool()
@timed_tool
def create_isosurface(value: float, field: str = None) -> str:
    """
    Extract a surface from a volumetric dataset at a specific scalar value using the Contour (marching cubes) filter. Every point on the resulting mesh has exactly that scalar value — all voxels above the threshold contribute a face. This is the primary technique for isolating structures by density: in CT data a high isovalue extracts bone, a lower one extracts soft tissue. The isovalue must be within the scalar range of the field. If field is None, the default scalar array is used. The contour result is a triangle surface, not a volume.

    Args:
        value: Scalar threshold at which to extract the surface.
        field: Name of the scalar array to contour. Auto-selected if None.
        
    Returns: Confirmation message with the registered filter name, or error description.
    """
    success, message, contour_obj, contour_name = pv_manager.create_isosurface(value, field)
    if success:
        # Return a user-friendly message that also includes the name
        return f"{message}. Filter registered as '{contour_name}'."
    else:
        return message

@mcp.tool()
@timed_tool
def create_slice(origin_x: float = None, origin_y: float = None, origin_z: float = None,
                 normal_x: float = 0, normal_y: float = 0, normal_z: float = 1) -> str:
    """
    Cut through the active dataset with an infinite plane and extract the 2D cross-section. The result shows scalar values mapped onto the cut surface, making internal structure visible without removing any geometry. The plane is defined by an origin point (any point on the plane) and a normal vector (perpendicular to the plane surface). If origin is omitted, the center of the dataset's bounding box is used. 
    
    Args: origin_x/y/z - A point on the slice plane. If None, defaults to dataset center. normal_x/y/z - Normal vector of the plane (default Z-axis [0,0,1]). 
    
    Returns: Confirmation with slice name and actual origin/normal used, or error description.
    """
    success, message, slice_filter, slice_name = pv_manager.create_slice(
        origin_x,
        origin_y,
        origin_z,
        normal_x,
        normal_y,
        normal_z
    )

    # Return either an error message or a success message including the slice's name
    return message if success else f"Error creating slice: {message}"

@mcp.tool()
@timed_tool
def toggle_volume_rendering(enable: bool = True) -> str:
    """
    Toggle the visibility of volume rendering for the active source. 
    
    Args: enable - Whether to show (True) or hide (False) volume rendering. If True, shows volume rendering (switching to 'Volume' representation if needed). If False, hides the volume but preserves the volume representation settings. 
    
    Returns: Status message.
    """
       
    success, message, source_name = pv_manager.create_volume_rendering(enable)
    if success:
        # Return a user-friendly message that also includes the name
        return f"{message}. Source registered as '{source_name}'."
    else:
        return message

@mcp.tool()
@timed_tool
def toggle_visibility(enable: bool = True) -> str:
    """
    Show or hide the active pipeline object in the render view without changing its representation type or any other settings. Hiding an object removes it from the rendered image while keeping it in the pipeline, it can be made visible again without reconfiguring. Useful for comparing two objects by toggling one off, or for decluttering the view while keeping filters intact.

    Args:
        enable: True to make the active source visible, False to hide it.
        
    Returns: Status message.
    """
       
    success, message, source_name = pv_manager.toggle_visibility(enable)
    if success:
        # Return a user-friendly message that also includes the name
        return f"{message}. Source registered as '{source_name}'."
    else:
        return message


@mcp.tool()
@timed_tool
def set_active_source(name: str) -> str:
    """
    Change which pipeline object is currently active. All filter operations,
    representation changes, transfer function edits, and data queries act on
    the active source. ParaView names sources automatically on creation
    (e.g. Contour1, Slice1, Calculator1). 

    Args:
        name: The exact registered name of the pipeline object (case-sensitive).
        
    Returns a status message.
    """
    success, message = pv_manager.set_active_source(name)
    return message

@mcp.tool()
@timed_tool
def get_active_source_names_by_type(source_type: str = None) -> str:
    """
    List all registered pipeline objects, optionally filtered by their ParaView class name. Returns the registered names that can be passed to set_active_source. When source_type is None, all pipeline objects are returned.

    Args: source_type - Filter sources by type (e.g., 'Sphere', 'Contour', etc.). If None, returns all sources. 
    
    Returns: A string message containing the source names or error message.
    """
    success, message, source_names = pv_manager.get_active_source_names_by_type(source_type)
    
    if success and source_names:
        sources_list = "\n- ".join(source_names)
        result = f"{message}:\n- {sources_list}"
        return result
    else:
        return message

@mcp.tool()
@timed_tool
def edit_volume_opacity(field_name: str, opacity_points: list[dict[str, float]]) -> str:
    """
    Set the opacity transfer function (OTF) for a named scalar field. The OTF is a piecewise linear curve that maps each scalar value to an opacity between 0.0 (fully transparent) and 1.0 (fully opaque). During volume rendering, every voxel's scalar value is looked up in this curve to determine how much it contributes to the final image. At least two control points are required; values are linearly interpolated between them. The values must be within the actual scalar range of the field. 
    
    Args: field_name - The scalar array name whose OTF is being modified. opacity_points - Control points as [{"value": float, "alpha": float}, ...]. 
    
    Returns: Confirmation that the OTF was updated, or error description.
    """
    formatted_points = [[pt["value"], pt["alpha"]] for pt in opacity_points]
    success, message = pv_manager.edit_volume_opacity(field_name, formatted_points)
    return message


@mcp.tool()
@timed_tool
def set_color_map(field_name: str, color_points: list[dict]) -> str:
    """
    Set a custom color transfer function (CTF) for a named scalar array. Use this only with volume rendering. Control points are value-RGB pairs; ParaView interpolates linearly between them. Before assigning colors, assess the scalar range using the default colormap (low -> blue, high -> red) to understand what values correspond to which structures. Lower scalar values indicate less dense material, higher values indicate denser material, assign colors accordingly. Values must be within the actual scalar range of the field. Always take a screenshot after calling this function. 
    
    Args: field_name - The scalar array name to apply the CTF to. color_points - List of {"value": float, "rgb": [r,g,b]} dicts where r,g,b belongs to [0.0, 1.0]. 
    
    Returns: Confirmation that the CTF was updated, or error description.
    """
    # Transform color_points to expected internal format: list[tuple[float, tuple[float, float, float]]]
    try:
        formatted_points = [(pt["value"], tuple(pt["rgb"])) for pt in color_points]
    except Exception as e:
        return f"Invalid format for color_points: {e}"

    success, message = pv_manager.set_color_map(field_name, formatted_points)
    return message


@mcp.tool()
@timed_tool
def apply_color_preset(preset_name: str = "Blue-Red") -> str:
    """
    Apply a built-in ParaView color preset to the lookup table of the currently active visualization. Presets are predefined perceptual color scales, for example, Viridis and Plasma are perceptually uniform and colorblind-friendly; Grayscale is useful for print; Cool to Warm encodes direction (negative/positive). This replaces whatever custom color mapping is currently set on the active representation's lookup table without modifying the opacity transfer function.

    Args:
        preset_name: Name of the ParaView preset. Common options: Blue-Red, Cool to Warm, Viridis, Plasma, Magma, Inferno, Rainbow, Grayscale.
                     
    Returns: Confirmation that the preset was applied, or error description.
    """
    success, message = pv_manager.apply_color_preset(preset_name)
    return message


@mcp.tool()
@timed_tool
def color_by(field: str, component: int = -1) -> str:
    """
    Color the active surface, mesh, or point-based representation by a scalar or vector field. This assigns the field's values to the display color lookup table so geometry is colored per-vertex or per-cell. This is intended for Surface, Wireframe, and Points representations. Volume representations use set_color_map and edit_volume_opacity instead, which operate on the transfer function rather than per-geometry coloring. The color scale is automatically rescaled to the full data range of the field.

    Args:
        field: Name of the scalar or vector array to color by. component: -1 for magnitude (default), 0 for X, 1 for Y, 2 for Z.
        
    Returns: Status message.
    """
    success, message = pv_manager.color_by(field, component)
    return message

@mcp.tool()
@timed_tool
def compute_surface_area() -> str:
    """
    Compute the total surface area of the active source. The active source must be a surface mesh, triangle cells with an 'Area' cell array produced by integration. Volumetric sources will not produce a valid area result. Isosurface outputs (from create_isosurface) and slice outputs are valid inputs. The result is the sum of all triangle areas in dataset units squared.
    """
    success, message, area_value = pv_manager.compute_surface_area()
    return message


@mcp.tool()
@timed_tool
def set_representation_type(rep_type: str) -> str:
    """
    Set the representation type for the active source. [Tips: This function should not be used for volume rendering]. 
    
    Args: rep_type - Representation type (Surface, Wireframe, Points, etc.). 
    
    Returns: Status message.
    """
    success, message = pv_manager.set_representation_type(rep_type)
    return message

@mcp.tool()
@timed_tool
def get_pipeline() -> str:
    """
    List all sources and filters currently registered in the ParaView pipeline, along with their internal class types. Each entry shows the registered name (used by set_active_source) and the ParaView filter class it represents. An empty pipeline means no data has been loaded or all objects were deleted.
    
    Returns: Description of the current pipeline.
    """
    success, message = pv_manager.get_pipeline()
    return message

@mcp.tool()
@timed_tool
def get_available_arrays() -> str:
    """
    List all scalar and vector data arrays on the active source, separated into point data (one value per vertex) and cell data (one value per element). Each array entry includes its name and number of components; 1-component arrays are scalars, 3-component arrays are typically vectors (velocity, gradient). The array names returned here are required as field_name or array_name parameters in other tools, always call this first when the available fields are unknown.
    """
    success, message = pv_manager.get_available_arrays()
    return message

@mcp.tool()
@timed_tool
def create_streamline(seed_point_number: int, vector_field: str = None,
                     integration_direction: str = "BOTH", max_steps: int = 1000,
                     initial_step: float = 0.1, maximum_step: float = 50.0) -> str:
    """
    Trace particle paths through a vector field using the StreamTracer filter, then wrap
    the resulting lines in tube geometry for visual thickness. Seed points are distributed
    automatically in a point cloud centered on the dataset's bounding box center. Each
    streamline follows the local vector direction by numerical integration. FORWARD traces
    along the field direction, BACKWARD traces against it, and BOTH traces in both directions
    from each seed. Requires at least one multi-component (vector) array in the active source;
    if vector_field is None, the first such array is used automatically.

    Args:
        seed_point_number: Number of seed points placed in the point cloud.
        vector_field: Name of the vector array to trace. Auto-detected if None.
        integration_direction: "FORWARD", "BACKWARD", or "BOTH" (default).
        max_steps: Maximum integration steps per streamline (default: 1000).
        initial_step: Initial step size (default: 0.1).
        maximum_step: Maximum streamline length before termination (default: 50.0).

    Returns: Status message.
    """
    # Call the stream tracer creation method in your ParaViewManager
    success, message, streamline, tube_name = pv_manager.create_stream_tracer(
        vector_field=vector_field,
        base_source=None,  # Use the active source
        point_center=None,  # Auto-calculate the center
        integration_direction=integration_direction,
        initial_step_length=initial_step,
        maximum_stream_length=maximum_step,
        number_of_streamlines=seed_point_number
    )
    
    if success:
        return f"{message} Tube registered as '{tube_name}'."
    else:
        return message

@mcp.tool()
@timed_tool
def get_screenshot() -> str:
    """
    Capture a PNG screenshot of the current ParaView render view, display it in chat, and return it as a base64-encoded string inside a JSON payload. The screenshot reflects exactly what is currently visible in the GUI viewport, all visible sources, the camera angle, lighting, and representation settings at the moment of capture.[tips] If the screenshot appears empty or blank, check that the active source has its visibility toggled on.
    
    Returns: JSON string with keys: "success" (bool), "data" (base64 PNG string), "path" (temp file path on disk), "media_type" ("image/png"). On failure: JSON with "success": false and "error" message.
    """
    success, message, img_path = pv_manager.get_screenshot()    

    if not success:
        return json.dumps({"success": False, "error": message})
    
    with open(img_path, "rb") as file:
        img_data = file.read();
        
    base64_encoded = base64.b64encode(img_data).decode()
    
    return json.dumps({"success": True, "data": base64_encoded, "path": img_path, "media_type": "image/png"})
 
@mcp.tool()
@timed_tool
def get_histogram(field: str = None, num_bins: int = 64, data_location: str = "POINTS") -> str:
    """
    Compute the frequency distribution of scalar values across the active source and display it as an ASCII bar chart. Each bin shows how many points (or cells) have values in that range, revealing where values cluster, the spread of the data, and whether there are distinct peaks. This distribution is the basis for designing meaningful opacity and color transfer functions, sparse bins can be made transparent while dense peaks at structures of interest are made opaque. If only one scalar array exists, field is auto-detected.

    Args:
        field: Array name to histogram. Auto-detected if the source has exactly one array.
        num_bins: Number of equal-width bins across the scalar range (default: 64).
        data_location: "POINTS" for vertex data, "CELLS" for cell-centered data (default: "POINTS").
        
    Returns: ASCII bar chart of the histogram.
    """

    success, message, histogram_data = pv_manager.get_histogram(field, num_bins, data_location)
    
    if not success or not histogram_data:
        return message
    
    max_freq = max(freq for  _ , freq in histogram_data) or 1
    bar_width = 30
    lines = [message, "", "Value       | Distribution"]
    lines.append("-" * 50)
    for center, freq in histogram_data:
        bar_len = int((freq / max_freq) * bar_width)
        lines.append(f"  {center:8.2f} | {'#' * bar_len} ({int(freq)})")
        
    
    return "\n".join(lines)

@mcp.tool()
@timed_tool
def get_active_source_state() -> str:
    """
    Return the display state of the currently active pipeline object: its registered name, internal ParaView class type, whether it is visible in the render view, the current representation mode (Surface, Volume, Wireframe, etc.), the overall opacity level, and which scalar array is currently driving its color mapping. Useful for confirming what is actually rendered and how before making changes.
    
    Returns: State report for the active source only.
    """
    success, message, s = pv_manager.get_active_source_state()
    if not success:
        return message
    return (
        f"Name: {s['name']} | Type: {s['type']} | Visible: {s['visible']}\n"
        f"Representation: {s['representation']} | Opacity: {s['opacity']}\n"
        f"Color array: {s['color_array'] or 'solid color'}"
    )
 
@mcp.tool()
@timed_tool
def get_data_bounds() -> str:
    """
    Return the spatial extents, center point, physical dimensions, total point and cell counts, and (for structured grids) the IJK index extent of the active dataset. Bounds are in the dataset's coordinate units. The center is the midpoint of the bounding box. For structured volumes, the grid extent shows how many voxels exist along each axis. These values are necessary inputs when placing slice planes, streamline seeds, probe points, or any geometry that requires world-space coordinates.
    """
    success, message, r = pv_manager.get_data_bounds()
    if not success:
        return message

    b = r['bounds']
    d = r['dimensions']
    c = r['center']

    lines = [
        "Data Bounds & Metadata",
        f"X: [{b['x']['min']:.4f}, {b['x']['max']:.4f}]  (size: {d['x']:.4f})",
        f"Y: [{b['y']['min']:.4f}, {b['y']['max']:.4f}]  (size: {d['y']:.4f})",
        f"Z: [{b['z']['min']:.4f}, {b['z']['max']:.4f}]  (size: {d['z']:.4f})",
        f"Center: ({c[0]:.4f}, {c[1]:.4f}, {c[2]:.4f})",
        f"Points: {r['number_of_points']}  |  Cells: {r['number_of_cells']}",
    ]

    if "extent" in r:
        e = r["extent"]
        lines.append(
            f"Grid Extent: "
            f"I[{e['i']['min']}, {e['i']['max']}] "
            f"J[{e['j']['min']}, {e['j']['max']}] "
            f"K[{e['k']['min']}, {e['k']['max']}]"
        )

    return "\n".join(lines)

def _gradient_histogram_chart(message, histogram_data):
    """Render a (bin_center, frequency) list as an ASCII bar chart under message."""
    max_freq = 1
    for _, freq in histogram_data:
        if freq > max_freq:
            max_freq = freq
    bar_width = 30
    lines = [message, "", "Gradient Magnitude | Distribution"]
    lines.append("-" * 55)
    for center, freq in histogram_data:
        bar_len = int((freq / max_freq) * bar_width)
        lines.append(f"  {center:10.4f} | {'#' * bar_len} ({int(freq)})")
    return "\n".join(lines)

@mcp.tool()
@timed_tool
def apply_gradient(field_name: str, result_array_name: str = "Gradient",
                   num_bins: int = 256, data_location: str = "POINTS") -> str:
    """
    Apply ParaView's Gradient filter to a named scalar array on the current active source, then
    compute and return the distribution of the resulting gradient magnitude as an ASCII bar chart.
    The Gradient filter produces a 3-component vector array (default name 'Gradient') giving the
    direction and rate of change of the field at every point. The magnitude of that vector measures
    how sharply the field changes at each location, so high magnitudes mark material boundaries and
    edges while low magnitudes mark flat, uniform regions. The magnitude histogram is computed
    server-side by selecting the 'Magnitude' component of the gradient array in the Histogram filter,
    so no intermediate scalar array is materialized and no volume data is transferred to the client.
    After this call the newly created Gradient filter is left as the active source, ready for volume
    rendering of the gradient field by shaping opacity on the gradient magnitude.

    Args:
        field_name: Name of the input scalar array to differentiate (for example 'ImageFile').
        result_array_name: Name to give the output 3-component gradient array (default 'Gradient').
        num_bins: Number of equal-width bins across the magnitude range (default: 256).
        data_location: 'POINTS' (default) or 'CELLS', selecting which association the input array lives on.

    Returns:
        ASCII bar chart of the gradient magnitude distribution with bin centers, bars scaled to the
        peak frequency, and raw counts per bin. The header line reports the bin count and the
        magnitude min and max. The Gradient filter becomes the active source as a side effect.
    """
    success, message, _proxy, _name, histogram_data = pv_manager.apply_gradient(
        field_name, result_array_name, num_bins, data_location)

    if not success or not histogram_data:
        return message

    return _gradient_histogram_chart(message, histogram_data)

@mcp.tool()
@timed_tool
def rotate_camera(azimuth: float = 30.0, elevation: float = 0.0) -> str:
    """
    Orbit the camera around the current focal point by the specified angles.
    Azimuth rotates horizontally around the vertical axis (left/right orbit). Elevation tilts the camera up or down relative to the focal point. Angles are in degrees; positive azimuth orbits counter-clockwise when viewed from above, positive elevation tilts upward. Rotations are incremental, calling this multiple times compounds the rotation. Use reset_camera to return to the default view fitting all data.

    Args:
        azimuth: Horizontal orbit angle in degrees (default: 30.0).
        elevation: Vertical tilt angle in degrees (default: 0.0).
    """
    success, message = pv_manager.rotate_camera(azimuth, elevation)
    return message

@mcp.tool()
@timed_tool
def reset_camera() -> str:
    """
    Reset the camera position, orientation, and zoom to fit all visible geometry in the render view. The camera moves to a default isometric-style vantage point that shows the full bounding box of all visible objects. Previously applied rotations or zoom levels from rotate_camera are discarded.
    """
    success, message = pv_manager.reset_camera()
    return message

@mcp.tool()
@timed_tool
def plot_over_line(point1: list[float] = None, point2: list[float] = None, resolution: int = 100) -> str:
    """
    Create a 'Plot Over Line' filter to sample data along a line between two points. 
    
    Args: point1 - [x,y,z] start point. If None, uses data bounds. point2 - [x,y,z] end point. If None, uses data bounds. resolution - Number of sample points (default: 100). 
    
    Returns: Status message.
    """
    success, message, plot_filter = pv_manager.plot_over_line(point1, point2, resolution)
    return message

@mcp.tool()
@timed_tool
def warp_by_vector(vector_field: str = None, scale_factor: float = 1.0) -> str:
    """
    Apply the 'Warp By Vector' filter to the active source. 
    
    Args: vector_field - Name of the vector field (auto-detected if None). scale_factor - Scale factor for the warp (default: 1.0). 
    
    Returns: Status message.
    """
    success, message, warp_filter = pv_manager.warp_by_vector(vector_field, scale_factor)
    return message

@mcp.tool()
@timed_tool
def clear_pipeline() -> str:
    """
    Delete every source and filter from the current ParaView pipeline, leaving a completely empty state. This removes all loaded data, all filters (contours, slices, calculators, etc.), and all associated display objects from the render view. The internal reference to the originally loaded data source is also cleared. This operation is irreversible, deleted pipeline objects cannot be recovered.
    """
    success, message = pv_manager.clear_pipeline()
    return message

@mcp.tool()
@timed_tool
def list_commands() -> str:
    """List all available commands in this ParaView MCP server."""
    commands = [
        # Data
        "load_data                    : Load data from a file (VTK, RAW, EXODUS, CSV, etc.)",
        "save_contour_as_stl          : Save the active surface/contour as an STL file",
        "get_available_arrays         : List all point and cell data arrays in the active source",
        "clear_pipeline               : Delete all sources and filters from the current pipeline.",

        # Sources & Filters
        "create_source                : Create a geometric source (Sphere, Cone, Cylinder, Plane, Box)",
        "create_isosurface            : Create an isosurface at a given scalar value",
        "create_slice                 : Slice the volume with a plane",
        "create_streamline            : Create streamline visualization from a vector field",
        "warp_by_vector               : Warp the active source by a vector field",
        "plot_over_line               : Sample and plot data along a line",

        # Volume Rendering
        "toggle_volume_rendering      : Enable or disable volume rendering",
        "edit_volume_opacity          : Set the scalar opacity transfer function",
        "set_color_map                : Set a custom RGB color transfer function for a named field",
        "apply_color_preset           : Apply a named color preset (Viridis, Blue-Red, etc.) to the active vis",
        "apply_gradient               : Apply the Gradient filter to a field and show its magnitude histogram",

        # Pipeline & State
        "get_pipeline                 : List all objects in the current pipeline",
        "get_active_source_state      : Get name, type, visibility, representation, opacity, color array of active source",
        "set_active_source            : Set the active pipeline object by name",
        "get_active_source_names_by_type : List pipeline objects filtered by type",
        "toggle_visibility            : Show or hide the active source",
        "set_representation_type      : Set representation (Surface, Wireframe, Points, Volume, etc.)",
        "color_by                     : Color the active source by a field",

        # Analysis
        "get_histogram                : Compute and display a histogram for a scalar field",
        "get_data_bounds              : Get bounding box, center, and dimensions of the active dataset",
        "compute_surface_area         : Compute the surface area of the active surface mesh",

        # Camera & Output
        "get_screenshot               : Capture a screenshot of the current view",
        "rotate_camera                : Rotate the camera by azimuth and elevation angles",
        "reset_camera                 : Reset the camera to fit all data",
    ]
    return "Available ParaView MCP commands:\n\n" + "\n".join(commands)


def main():
    parser = argparse.ArgumentParser(description="ParaView External MCP Server")
    parser.add_argument("--server", type=str, default="localhost", help="ParaView server hostname (default: localhost)")
    parser.add_argument("--port", type=int, default=11111, help="ParaView server port (default: 11111)")
    parser.add_argument("--paraview_package_path", type=str, help="Path to the ParaView Python package", default=None)
    
    args = parser.parse_args()

    # Add the ParaView package path to sys.path
    if args.paraview_package_path:
        sys.path.append(args.paraview_package_path)
    
    # Connect to ParaView
    pv_manager.connect(args.server, args.port)
    
    # Run the MCP server
    try:
        logger.info("Starting ParaView External MCP Server")
        logger.info(f"ParaView server: {args.server}:{args.port}")
        # logger.info("Default prompt enabled: Claude will call one function per reply")
        
        # Run the MCP server
        mcp.run()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Error running MCP server: {str(e)}")

if __name__ == "__main__":
    main()
