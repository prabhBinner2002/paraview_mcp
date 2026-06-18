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
        logging.FileHandler(log_file),
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

# Create the ParaView manager
pv_manager = ParaViewManager()

# Initialize FastMCP server
mcp = FastMCP("ParaView")

# ============================================================================
# MCP Tools for ParaView
# ============================================================================

@mcp.tool()
def load_data(file_path: str) -> str:
    """
    Load a data file into ParaView and register it as a source in the pipeline.
    Supports VTK, VTU, VTS, EXODUS, CSV, RAW binary volumes, and other formats
    ParaView natively reads. For RAW files, dimensions and scalar type are
    auto-detected from the filename (e.g. foot_256x256x256_uint8.raw).
    The loaded source becomes the active source and is shown in the render view.

    Args:
        file_path: Absolute path to the file to load.
    """
    success, message, _, source_name = pv_manager.load_data(file_path)
    if success:
        return f"{message}. Source registered as '{source_name}'."
    else:
        return message

@mcp.tool()
def save_contour_as_stl(stl_filename: str = "contour.stl") -> str:
    """
    Export the currently active source as an STL triangle mesh file.
    The file is written to the same directory as the originally loaded data.
    Works on any source that produces a surface — isosurfaces, slices, extracted surfaces,
    or any geometry with triangle cells. Volumetric sources (vtkImageData) cannot be
    directly exported as STL; apply a Contour or ExtractSurface filter first.

    Args:
        stl_filename: Output filename including .stl extension (default: 'contour.stl').
    """
    success, message, path = pv_manager.save_contour_as_stl(stl_filename)
    return message

@mcp.tool()
def create_source(source_type: str) -> str:
    """
    Create a parametric geometric source and add it to the pipeline.
    These are synthetic objects with no associated scalar data — useful for
    reference geometry, clipping planes, or testing visualization settings.
    The created source becomes the active source and is shown in the render view.

    Args:
        source_type: One of Sphere, Cone, Cylinder, Plane, Box.
    """
    success, message, _, source_name = pv_manager.create_source(source_type)
    if success:
        return f"{message}. Source registered as '{source_name}'."
    else:
        return message

@mcp.tool()
def create_isosurface(value: float, field: str = None) -> str:
    """
    Extract a surface from a volumetric dataset at a specific scalar value using
    the Contour (marching cubes) filter. Every point on the resulting mesh has
    exactly that scalar value — all voxels above the threshold contribute a face.
    This is the primary technique for isolating structures by density: in CT data
    a high isovalue (e.g. 1275) extracts bone, a lower one extracts soft tissue.
    The isovalue must be within the scalar range of the field; use get_histogram
    to identify meaningful thresholds. If field is None, the default scalar array
    is used. The contour result is a triangle surface, not a volume.

    Args:
        value: Scalar threshold at which to extract the surface.
        field: Name of the scalar array to contour. Auto-selected if None.
    """
    success, message, contour_obj, contour_name = pv_manager.create_isosurface(value, field)
    if success:
        # Return a user-friendly message that also includes the name
        return f"{message}. Filter registered as '{contour_name}'."
    else:
        return message

@mcp.tool()
def create_slice(origin_x: float = None, origin_y: float = None, origin_z: float = None,
                 normal_x: float = 0, normal_y: float = 0, normal_z: float = 1) -> str:
    """
    Cut through the active dataset with an infinite plane and extract the 2D cross-section.
    The result shows scalar values mapped onto the cut surface, making internal structure
    visible without removing any geometry. The plane is defined by an origin point (any
    point on the plane) and a normal vector (perpendicular to the plane surface).
    Normal [0,0,1] gives a horizontal XY cut, [1,0,0] an YZ cut, [0,1,0] an XZ cut.
    If origin is omitted, the center of the dataset's bounding box is used.
    Use get_data_bounds to find exact coordinate ranges before choosing an origin.

    Args:
        origin_x, origin_y, origin_z: A point on the slice plane. Defaults to dataset center.
        normal_x, normal_y, normal_z: Normal vector of the plane (default Z-axis [0,0,1]).
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
def toggle_volume_rendering(enable: bool = True) -> str:
    """
    Switch the originally loaded source to Volume representation and control its visibility.
    Volume rendering ray-casts through the entire dataset, accumulating color and opacity
    at every voxel along each sight line. Unlike isosurfaces, it shows the full interior
    simultaneously — every voxel contributes based on its opacity transfer function value.
    The opacity and color transfer functions (edit_volume_opacity, set_color_map) determine
    which scalar ranges appear transparent or opaque. Setting enable=False hides the volume
    without destroying the representation settings, so it can be re-shown without reconfiguring.

    Args:
        enable: True to switch to Volume representation and show it, False to hide it.
    """
       
    success, message, source_name = pv_manager.create_volume_rendering(enable)
    if success:
        # Return a user-friendly message that also includes the name
        return f"{message}. Source registered as '{source_name}'."
    else:
        return message

@mcp.tool()
def toggle_visibility(enable: bool = True) -> str:
    """
    Show or hide the active pipeline object in the render view without changing
    its representation type or any other settings. Hiding an object removes it
    from the rendered image while keeping it in the pipeline — it can be made
    visible again without reconfiguring. Useful for comparing two objects by
    toggling one off, or for decluttering the view while keeping filters intact.

    Args:
        enable: True to make the active source visible, False to hide it.
    """
       
    success, message, source_name = pv_manager.toggle_visibility(enable)
    if success:
        # Return a user-friendly message that also includes the name
        return f"{message}. Source registered as '{source_name}'."
    else:
        return message


@mcp.tool()
def set_active_source(name: str) -> str:
    """
    Change which pipeline object is currently active. All filter operations,
    representation changes, transfer function edits, and data queries act on
    the active source. ParaView names sources automatically on creation
    (e.g. Contour1, Slice1, Calculator1). Use get_pipeline to see all
    registered names, or get_active_source_names_by_type to filter by type.

    Args:
        name: The exact registered name of the pipeline object (case-sensitive).
    """
    success, message = pv_manager.set_active_source(name)
    return message

@mcp.tool()
def get_active_source_names_by_type(source_type: str = None) -> str:
    """
    List all registered pipeline objects, optionally filtered by their ParaView class name.
    Returns the registered names that can be passed to set_active_source.
    The type string is matched as a substring of the internal class name, so
    'Contour' matches ContourFilter, 'Slice' matches Slice, etc.
    When source_type is None, all pipeline objects are returned.

    Args:
        source_type: Partial class name to filter by (e.g. 'Contour', 'Slice', 'Calculator').
                     None returns every object in the pipeline.
    """
    success, message, source_names = pv_manager.get_active_source_names_by_type(source_type)
    
    if success and source_names:
        sources_list = "\n- ".join(source_names)
        result = f"{message}:\n- {sources_list}"
        return result
    else:
        return message

@mcp.tool()
def edit_volume_opacity(field_name: str, opacity_points: list[dict[str, float]]) -> str:
    """
    Set the 1D scalar opacity transfer function (OTF) for a named field.
    The OTF is a piecewise linear curve that maps each scalar value to an opacity
    between 0.0 (fully transparent) and 1.0 (fully opaque). During volume rendering,
    every voxel's scalar value is looked up in this curve to determine how much it
    contributes to the final image. Sparse regions (air, background) are typically
    made transparent with alpha=0.0, while structures of interest are given higher
    opacity. At least two control points are required; ParaView linearly interpolates
    between them. The values must be within the actual scalar range of the field —
    use get_histogram to identify the data range and value distribution.

    Args:
        field_name: The scalar array name whose OTF is being modified (e.g. 'ImageScalars', 'Grad_Magnitude').
        opacity_points: Control points as [{"value": float, "alpha": float}, ...].
                        value is the scalar value, alpha is opacity in [0.0, 1.0].
    """
    formatted_points = [[pt["value"], pt["alpha"]] for pt in opacity_points]
    success, message = pv_manager.edit_volume_opacity(field_name, formatted_points)
    return message


@mcp.tool()
def set_color_map(field_name: str, color_points: list[dict]) -> str:
    """
    Set a custom piecewise linear color transfer function (CTF) for a specific named
    scalar array in ParaView. The CTF maps scalar values to RGB colors, controlling
    what color each density or value level receives in volume rendering. Color points
    are defined as (value, RGB) pairs; ParaView interpolates linearly between them.
    In CT/density data, lower scalar values typically correspond to less dense material
    (air, fluid) and higher values to denser material (bone, metal). The default ParaView
    colormap maps low→blue and high→red. A custom CTF lets you assign perceptually
    meaningful colors — e.g. bone as white/ivory, soft tissue as pink, air as fully
    transparent (handled separately via edit_volume_opacity). Values must be within
    the actual scalar range of the named field.

    Args:
        field_name: The scalar array name to apply the CTF to (e.g. 'ImageScalars').
        color_points: List of {"value": float, "rgb": [r, g, b]} dicts where r,g,b ∈ [0.0, 1.0].
    """
    # Transform color_points to expected internal format: list[tuple[float, tuple[float, float, float]]]
    try:
        formatted_points = [(pt["value"], tuple(pt["rgb"])) for pt in color_points]
    except Exception as e:
        return f"Invalid format for color_points: {e}"

    success, message = pv_manager.set_color_map(field_name, formatted_points)
    return message


@mcp.tool()
def apply_color_preset(preset_name: str = "Blue-Red") -> str:
    """
    Apply a built-in ParaView color preset to the lookup table of the currently
    active visualization. Presets are predefined perceptual color scales — for
    example, Viridis and Plasma are perceptually uniform and colorblind-friendly;
    Grayscale is useful for print; Cool to Warm encodes direction (negative/positive).
    This replaces whatever custom color mapping is currently set on the active
    representation's lookup table without modifying the opacity transfer function.

    Args:
        preset_name: Name of the ParaView preset. Common options: Blue-Red, Cool to Warm,
                     Viridis, Plasma, Magma, Inferno, Rainbow, Grayscale.
    """
    success, message = pv_manager.apply_color_preset(preset_name)
    return message


@mcp.tool()
def color_by(field: str, component: int = -1) -> str:
    """
    Color the active surface, mesh, or point-based representation by a scalar or
    vector field. This assigns the field's values to the display color lookup table
    so geometry is colored per-vertex or per-cell. For vector fields, component=-1
    uses the magnitude, or pass 0/1/2 for X/Y/Z components individually.
    This is intended for Surface, Wireframe, and Points representations.
    Volume representations use set_color_map and edit_volume_opacity instead,
    which operate on the transfer function rather than per-geometry coloring.
    The color scale is automatically rescaled to the full data range of the field.

    Args:
        field: Name of the scalar or vector array to color by.
        component: -1 for magnitude (default), 0 for X, 1 for Y, 2 for Z.
    """
    success, message = pv_manager.color_by(field, component)
    return message

@mcp.tool()
def compute_surface_area() -> str:
    """
    Compute the total surface area of the active source by integrating cell areas
    using the IntegrateVariables filter. The active source must be a surface mesh —
    triangle cells with an 'Area' cell array produced by integration. Volumetric
    sources (vtkImageData, unstructured grids without surface cells) will not produce
    a valid area result. Isosurface outputs (from create_isosurface) and slice outputs
    are valid inputs. The result is the sum of all triangle areas in dataset units squared.
    """
    success, message, area_value = pv_manager.compute_surface_area()
    return message


@mcp.tool()
def set_representation_type(rep_type: str) -> str:
    """
    Set how the active source's geometry is drawn in the render view.
    Surface renders filled triangles, Wireframe renders only edges, Points renders
    only vertices, Volume enables ray-cast volume rendering (same effect as
    toggle_volume_rendering), and Outline renders only the bounding box.
    Changing representation type does not alter the underlying data or pipeline —
    it only affects the display. Transfer function settings (opacity, color map)
    apply specifically to Volume mode.

    Args:
        rep_type: One of Surface, Wireframe, Points, Volume, Surface With Edges, Outline.
    """
    success, message = pv_manager.set_representation_type(rep_type)
    return message

@mcp.tool()
def get_pipeline() -> str:
    """
    List all sources and filters currently registered in the ParaView pipeline,
    along with their internal class types. Each entry shows the registered name
    (used by set_active_source) and the ParaView filter class it represents.
    An empty pipeline means no data has been loaded or all objects were deleted.
    """
    success, message = pv_manager.get_pipeline()
    return message

@mcp.tool()
def get_available_arrays() -> str:
    """
    List all scalar and vector data arrays on the active source, separated into
    point data (one value per vertex) and cell data (one value per element).
    Each array entry includes its name and number of components — 1-component
    arrays are scalars, 3-component arrays are typically vectors (velocity, gradient).
    These names are required parameters for tools like color_by, edit_volume_opacity,
    set_color_map, get_histogram, get_gradient_stats, and get_gradient_histogram.
    """
    success, message = pv_manager.get_available_arrays()
    return message

@mcp.tool()
def create_streamline(seed_point_number: int, vector_field: str = None,
                     integration_direction: str = "BOTH", max_steps: int = 1000,
                     initial_step: float = 0.1, maximum_step: float = 50.0) -> str:
    """
    Trace particle paths through a vector field using the StreamTracer filter,
    then wrap the resulting lines in tube geometry for visual thickness.
    Seed points are distributed automatically in a point cloud centered on the
    dataset's bounding box center. Each streamline follows the local vector
    direction by numerical integration — FORWARD follows the field, BACKWARD
    traces against it, BOTH does both from each seed. The result shows flow
    patterns, circulation, or any directed field structure. Requires at least
    one multi-component (vector) array in the active source; if vector_field
    is None, the first array with more than one component is used automatically.

    Args:
        seed_point_number: Number of seed points placed in the point cloud.
        vector_field: Name of the vector array to trace. Auto-detected if None.
        integration_direction: "FORWARD", "BACKWARD", or "BOTH" (default).
        max_steps: Maximum integration steps per streamline (default: 1000).
        initial_step: Initial step size (default: 0.1).
        maximum_step: Maximum streamline length before termination (default: 50.0).
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
def get_screenshot() -> str:
    """
    Capture a PNG screenshot of the current ParaView render view and return it
    as a base64-encoded string inside a JSON payload. The screenshot reflects
    exactly what is currently visible in the GUI viewport — all visible sources,
    the camera angle, lighting, and representation settings at the moment of capture.
    The returned JSON contains keys: "success" (bool), "data" (base64 PNG string),
    "path" (temp file path), and "media_type" ("img/png").
    """
    success, message, img_path = pv_manager.get_screenshot()    

    if not success:
        return json.dumps({"success": False, "error": message})
    
    with open(img_path, "rb") as file:
        img_data = file.read();
        
    base64_encoded = base64.b64encode(img_data).decode()
    
    return json.dumps({"success": True, "data": base64_encoded, "path": img_path, "media_type": "img/png"})
 
@mcp.tool()
def get_histogram(field: str = None, num_bins: int = 64, data_location: str = "POINTS") -> str:
    """
    Compute the frequency distribution of scalar values across the active source
    and display it as an ASCII bar chart. Each bin shows how many points (or cells)
    have values in that range, revealing where values cluster, the spread of the data,
    and whether there are distinct peaks (e.g. air, tissue, bone in CT data).
    This distribution is the basis for designing meaningful opacity and color transfer
    functions — sparse bins can be made transparent while dense peaks at structures
    of interest are made opaque. If only one scalar array exists, field is auto-detected.

    Args:
        field: Array name to histogram. Auto-detected if the source has exactly one array.
        num_bins: Number of equal-width bins across the scalar range (default: 64).
        data_location: "POINTS" for vertex data, "CELLS" for cell-centered data (default: "POINTS").
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
def get_active_source_state() -> str:
    """
    Return the display state of the currently active pipeline object: its registered
    name, internal ParaView class type, whether it is visible in the render view,
    the current representation mode (Surface, Volume, Wireframe, etc.), the overall
    opacity level, and which scalar array is currently driving its color mapping.
    Useful for confirming what is actually rendered and how before making changes.
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
def get_data_bounds() -> str:
    """
    Return the spatial extents, center point, physical dimensions, total point
    and cell counts, and (for structured grids) the IJK index extent of the active
    dataset. Bounds are in the dataset's coordinate units. The center is the midpoint
    of the bounding box. For structured volumes, the grid extent shows how many voxels
    exist along each axis. These values are necessary inputs when placing slice planes,
    streamline seeds, probe points, or any geometry that requires world-space coordinates.
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

@mcp.tool()
def get_gradient_stats(field_name: str) -> str:
    """
    Compute the spatial gradient vector at every point of the named scalar field,
    then calculate the gradient magnitude (L2 norm of the gradient vector) and return
    its minimum and maximum values across the entire dataset. The gradient at each
    point measures how rapidly the scalar changes in 3D space — a magnitude near zero
    means a flat, uniform region; a large magnitude means a sharp boundary or edge.
    Implemented as Gradient filter (produces a 3-component vector per point) followed
    by a Calculator filter computing mag() of that vector. The result tells you the
    full range of gradient magnitudes in the data, which is needed to design meaningful
    opacity control points when volume rendering by gradient magnitude.

    Args:
        field_name: Name of the scalar array to differentiate (e.g. 'ImageScalars').
    """
    success, message, stats = pv_manager.get_gradient_stats(field_name)
    if not success:
        return message
    return (
        f"Gradient magnitude for '{field_name}':\n"
        f"  min = {stats['min']:.4g}\n"
        f"  max = {stats['max']:.4g}\n"
        f"Use get_gradient_histogram to see how gradient values are distributed."
    )

@mcp.tool()
def get_gradient_histogram(field_name: str, num_bins: int = 64) -> str:
    """
    Compute the spatial gradient at every point of the named scalar field, calculate
    the gradient magnitude (a single scalar per point representing how sharply the
    field changes at that location), and return the frequency distribution of those
    magnitudes as an ASCII bar chart. The pipeline built is: active source →
    Gradient filter (produces per-point 3D gradient vector) → Calculator filter
    (computes mag() to get a scalar magnitude) → Histogram filter (bins the magnitudes).
    After this call, the active source in the pipeline is set to the Calculator output,
    which holds the 'Grad_Magnitude' array. This means toggle_volume_rendering and
    edit_volume_opacity('Grad_Magnitude', ...) act on the gradient magnitude volume,
    making sharp boundaries opaque and uniform flat regions transparent — a surface
    detection approach driven entirely by rate-of-change rather than absolute value.

    Args:
        field_name: Name of the scalar array to differentiate (e.g. 'ImageScalars').
        num_bins: Number of equal-width histogram bins across the magnitude range (default: 64).
    """
    success, message, histogram_data = pv_manager.get_gradient_histogram(field_name, num_bins)

    if not success or not histogram_data:
        return message

    max_freq = max(freq for _, freq in histogram_data) or 1
    bar_width = 30
    lines = [message, "", "Gradient Magnitude | Distribution"]
    lines.append("-" * 55)
    for center, freq in histogram_data:
        bar_len = int((freq / max_freq) * bar_width)
        lines.append(f"  {center:10.4f} | {'#' * bar_len} ({int(freq)})")

    return "\n".join(lines)

@mcp.tool()
def rotate_camera(azimuth: float = 30.0, elevation: float = 0.0) -> str:
    """
    Orbit the camera around the current focal point by the specified angles.
    Azimuth rotates horizontally around the vertical axis (left/right orbit).
    Elevation tilts the camera up or down relative to the focal point.
    Angles are in degrees; positive azimuth orbits counter-clockwise when viewed
    from above, positive elevation tilts upward. Rotations are incremental —
    calling this multiple times compounds the rotation. Use reset_camera to
    return to the default view fitting all data.

    Args:
        azimuth: Horizontal orbit angle in degrees (default: 30.0).
        elevation: Vertical tilt angle in degrees (default: 0.0).
    """
    success, message = pv_manager.rotate_camera(azimuth, elevation)
    return message

@mcp.tool()
def reset_camera() -> str:
    """
    Reset the camera position, orientation, and zoom to fit all visible geometry
    in the render view. The camera moves to a default isometric-style vantage point
    that shows the full bounding box of all visible objects. Previously applied
    rotations or zoom levels from rotate_camera are discarded.
    """
    success, message = pv_manager.reset_camera()
    return message

@mcp.tool()
def plot_over_line(point1: list[float] = None, point2: list[float] = None, resolution: int = 100) -> str:
    """
    Sample all scalar and vector arrays along a straight line through the active
    dataset and display the results in a new XY chart view. The line is defined by
    two world-space endpoints; ParaView interpolates the field values at evenly
    spaced probe points along it. This is the standard method for extracting a 1D
    profile through a 3D volume — useful for measuring how density, temperature,
    pressure, or velocity changes along a chosen path. If endpoints are omitted,
    ParaView uses the dataset's bounding box diagonal. Use get_data_bounds first
    to obtain accurate world-space coordinates for the endpoints.

    Args:
        point1: [x, y, z] start point in world coordinates. Defaults to dataset bounds start.
        point2: [x, y, z] end point in world coordinates. Defaults to dataset bounds end.
        resolution: Number of evenly spaced sample points along the line (default: 100).
    """
    success, message, plot_filter = pv_manager.plot_over_line(point1, point2, resolution)
    return message

@mcp.tool()
def warp_by_vector(vector_field: str = None, scale_factor: float = 1.0) -> str:
    """
    Displace each point of the active source's geometry by a vector field, scaling
    the displacement by scale_factor. Each vertex is moved by (vector * scale_factor)
    in world space, deforming the mesh to visually encode the vector magnitude and
    direction as physical shape change. This is commonly used to visualize structural
    deformation (displacement fields from FEM simulations), flow-induced shape change,
    or any dataset where a vector array represents per-point offset. The original
    geometry is replaced in the view by the warped version. If vector_field is None,
    the first array with more than one component is used automatically.

    Args:
        vector_field: Name of the 3-component vector array to warp by. Auto-detected if None.
        scale_factor: Multiplier applied to the displacement vectors (default: 1.0).
    """
    success, message, warp_filter = pv_manager.warp_by_vector(vector_field, scale_factor)
    return message

@mcp.tool()
def clear_pipeline() -> str:
    """
    Delete every source and filter from the current ParaView pipeline, leaving
    a completely empty state. This removes all loaded data, all filters (contours,
    slices, calculators, etc.), and all associated display objects from the render view.
    The internal reference to the originally loaded data source is also cleared.
    This operation is irreversible — deleted pipeline objects cannot be recovered.
    """
    success, message = pv_manager.clear_pipeline()
    return message

@mcp.tool()
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
        "get_gradient_stats           : Compute gradient magnitude stats (min/max) for a field",
        "get_gradient_histogram       : Compute gradient magnitude at every point and show its histogram",

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
