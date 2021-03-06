
#
# This source file is part of appleseed.
# Visit http://appleseedhq.net/ for additional information and resources.
#
# This software is released under the MIT license.
#
# Copyright (c) 2014-2017 The appleseedhq Organization
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#

import array
import os
import struct
import subprocess
import shutil
import tempfile
import threading
from shutil import copyfile

import bpy

from . import projectwriter
from . import util


class RenderAppleseed(bpy.types.RenderEngine):
    bl_idname = 'APPLESEED_RENDER'
    bl_label = 'appleseed'
    bl_use_preview = True

    # This lock allows to serialize renders.
    render_lock = threading.Lock()

    def __init__(self):
        pass

    def update(self, data, scene):
        pass

    def render(self, scene):
        with RenderAppleseed.render_lock:
            if self.is_preview:
                if not bpy.app.background:
                    self.__render_material_preview(scene)
            else:
                self.__render_scene(scene)

    def __render_scene(self, scene):
        """
        Export and render the scene.
        """

        # Name and location of the exported project.
        project_dir = os.path.join(tempfile.gettempdir(), "blenderseed", "render")
        project_filepath = os.path.join(project_dir, "render.appleseed")

        # Create target directories if necessary.
        if not os.path.exists(project_dir):
            try:
                os.makedirs(project_dir)
            except os.error:
                self.report({"ERROR"}, "The directory {0} could not be created. Check directory permissions.".format(project_dir))
                return

        # Generate project on disk.
        writer = projectwriter.Writer()
        writer.write(scene, project_filepath)

        # Render project.
        self.__render_project_file(scene, project_filepath, project_dir)

    def __render_material_preview(self, scene):
        """
        Export and render the material preview scene.
        """

        # Don't render material thumbnails.
        (width, height) = util.get_render_resolution(scene)
        if width <= 96:
            return

        # Collect objects and their materials in a object -> [materials] dictionary.
        objects_materials = {}
        for obj in (obj for obj in scene.objects if obj.is_visible(scene) and not obj.hide_render):
            for mat in util.get_instance_materials(obj):
                if mat is not None:
                    if obj.name not in objects_materials.keys():
                        objects_materials[obj] = []
                    objects_materials[obj].append(mat)

        # Find objects that are likely to be the preview objects.
        preview_objects = [o for o in objects_materials.keys() if o.name.startswith('preview')]
        if not preview_objects:
            return

        # Find the materials attached to the likely preview object.
        likely_materials = objects_materials[preview_objects[0]]
        if not likely_materials:
            return

        # Build the path to the output preview project.
        preview_output_dir = os.path.join(tempfile.gettempdir(), "blenderseed", "material_preview")
        preview_project_filepath = os.path.join(preview_output_dir, "material_preview.appleseed")

        # Create target directories if necessary.
        if not os.path.exists(preview_output_dir):
            try:
                os.makedirs(preview_output_dir)
            except os.error:
                self.report({"ERROR"}, "The directory {0} could not be created. Check directory permissions.".format(preview_output_dir))
                return

        # Copy assets from template project to output directory.
        preview_template_dir = os.path.join(os.sep.join(util.realpath(__file__).split(os.sep)[:-1]), "mat_preview")
        existing_files = os.listdir(preview_output_dir)
        for item in os.listdir(preview_template_dir):
            if item not in existing_files:
                copyfile(os.path.join(preview_template_dir, item), os.path.join(preview_output_dir, item))

        prev_mat = likely_materials[0]
        prev_type = prev_mat.preview_render_type.lower()

        # Export the project.
        writer = projectwriter.Writer()
        file_written = writer.export_preview(scene,
                                             preview_project_filepath,
                                             prev_mat,
                                             prev_type,
                                             width,
                                             height)
        if not file_written:
            print('Error while exporting. Check the console for details.')
            return

        # Render the project.
        self.__render_project_file(scene, preview_project_filepath)

    def __render_project_file(self, scene, project_filepath, project_dir=None):
        # Check that the path to the bin folder is set.
        appleseed_bin_dir = bpy.context.user_preferences.addons['blenderseed'].preferences.appleseed_binary_directory
        if not appleseed_bin_dir:
            self.report({'ERROR'}, "The path to the folder containing the appleseed.cli executable has not been specified. Set the path in the add-on user preferences.")
            return

        # Properly handle relative Blender paths.
        appleseed_bin_dir = util.realpath(appleseed_bin_dir)

        # Check that the path to the bin folder indeed points to a folder.
        if not os.path.isdir(appleseed_bin_dir):
            self.report({'ERROR'}, "The path to the folder containing the appleseed.cli executable was set to {0} but this does not appear to be a valid folder.".format(appleseed_bin_dir))
            return

        # Compute the path to the appleseed.cli executable.
        appleseed_bin_path = os.path.join(appleseed_bin_dir, "appleseed.cli")

        # Compute render resolution.
        (width, height) = util.get_render_resolution(scene)

        # Rendered pixel total
        self.rendered_pixels = 0

        # Compute render window.
        if scene.render.use_border:
            min_x = int(scene.render.border_min_x * width)
            min_y = height - int(scene.render.border_max_y * height)
            max_x = int(scene.render.border_max_x * width) - 1
            max_y = height - int(scene.render.border_min_y * height) - 1
        else:
            min_x = 0
            min_y = 0
            max_x = width - 1
            max_y = height - 1

        # Compute total pixel count.
        self.total_pixels = (max_x - min_x + 1) * (max_y - min_y + 1) * scene.appleseed.renderer_passes

        # Launch appleseed.cli.
        threads = 'auto' if scene.appleseed.threads_auto else str(scene.appleseed.threads)
        cmd = (appleseed_bin_path,
               project_filepath,
               '--to-stdout',
               '--threads', threads,
               '--message-verbosity', 'warning',
               '--resolution', str(width), str(height),
               '--window', str(min_x), str(min_y), str(max_x), str(max_y))
        try:
            process = subprocess.Popen(cmd, cwd=appleseed_bin_dir, env=os.environ.copy(), stdout=subprocess.PIPE)
        except OSError as e:
            self.report({'ERROR'}, "Failed to run {0} with project {1}: {2}.".format(appleseed_bin_path, project_filepath, e))
            return

        self.update_stats("", "appleseed: Rendering")

        # Update while rendering.
        while not self.test_break():
            # Wait for the next chunk header from the process's stdout.
            chunk_header_data = os.read(process.stdout.fileno(), 2 * 4)
            if not chunk_header_data:
                break

            # Decode chunk header.
            chunk_header = struct.unpack("II", chunk_header_data)
            chunk_type = chunk_header[0]
            chunk_size = chunk_header[1]

            if chunk_type == 1:
                # Tile data (protocol v1).
                if not self.__process_tile_data_chunk(process, min_x, min_y, max_x, max_y):
                    break
            elif chunk_type == 2:
                # Tile highlight (protocol v1).
                if not self.__process_tile_highlight_chunk(process, min_x, min_y, max_x, max_y):
                    break
            else:
                # Ignore unknown chunks.
                os.read(process.stdout.fileno(), chunk_size)
                continue

        # Make sure the appleseed.cli process has terminated.
        process.kill()

        if scene.appleseed.clean_cache:
            if os.path.exists(project_dir):
                try:
                    shutil.rmtree(project_dir)
                    self.report({'INFO'}, "Render Cache Deleted")
                except:
                    pass

    def __process_tile_data_chunk(self, process, min_x, min_y, max_x, max_y):
        # Read and decode tile header.
        tile_header = struct.unpack("IIIII", os.read(process.stdout.fileno(), 5 * 4))
        tile_x = tile_header[0]
        tile_y = tile_header[1]
        tile_w = tile_header[2]
        tile_h = tile_header[3]
        tile_c = tile_header[4]

        # Read tile data.
        tile_size = tile_w * tile_h * tile_c * 4
        tile_data = bytes()
        while len(tile_data) < tile_size and not self.test_break():
            tile_data += os.read(process.stdout.fileno(), tile_size - len(tile_data))
        if self.test_break():
            return False

        # Optional debug message.
        if False:
            print("Received tile: x={0} y={1} w={2} h={3} c={4}".format(tile_x, tile_y, tile_w, tile_h, tile_c))

        # Ignore tiles completely outside the render window.
        if tile_x > max_x or tile_x + tile_w - 1 < min_x:
            return True
        if tile_y > max_y or tile_y + tile_h - 1 < min_y:
            return True

        # Image-space coordinates of the intersection between the tile and the render window.
        ix0 = max(tile_x, min_x)
        iy0 = max(tile_y, min_y)
        ix1 = min(tile_x + tile_w - 1, max_x)
        iy1 = min(tile_y + tile_h - 1, max_y)

        # Number of rows and columns to skip in the input tile.
        skip_x = ix0 - tile_x
        skip_y = iy0 - tile_y
        take_x = ix1 - ix0 + 1
        take_y = iy1 - iy0 + 1

        # Extract relevant tile data and convert them to the format expected by Blender.
        floats = array.array('f')
        floats.fromstring(tile_data)
        pix = []
        for y in range(take_y - 1, -1, -1):
            start_pix = (skip_y + y) * tile_w + skip_x
            end_pix = start_pix + take_x
            pix.extend(floats[p * 4:p * 4 + 4] for p in range(start_pix, end_pix))

        # Window-space coordinates of the intersection between the tile and the render window.
        x0 = ix0 - min_x    # left
        y0 = max_y - iy1    # bottom

        # Update image.
        result = self.begin_result(x0, y0, take_x, take_y)
        layer = result.layers[0].passes[0]
        layer.rect = pix
        self.end_result(result)

        # Update progress bar.
        self.rendered_pixels += take_x * take_y
        self.update_progress(self.rendered_pixels / self.total_pixels)

        return True

    def __process_tile_highlight_chunk(self, process, min_x, min_y, max_x, max_y):
        # Read and decode tile header.
        tile_header = struct.unpack("IIII", os.read(process.stdout.fileno(), 4 * 4))
        tile_x = tile_header[0]
        tile_y = tile_header[1]
        tile_w = tile_header[2]
        tile_h = tile_header[3]

        # Ignore tiles completely outside the render window.
        if tile_x > max_x or tile_x + tile_w - 1 < min_x:
            return True
        if tile_y > max_y or tile_y + tile_h - 1 < min_y:
            return True

        # Image-space coordinates of the intersection between the tile and the render window.
        ix0 = max(tile_x, min_x)
        iy0 = max(tile_y, min_y)
        ix1 = min(tile_x + tile_w - 1, max_x)
        iy1 = min(tile_y + tile_h - 1, max_y)

        # Window-space coordinates of the intersection between the tile and the render window.
        x0 = ix0 - min_x    # left
        x1 = ix1 - min_x    # right
        y0 = max_y - iy1    # bottom
        y1 = max_y - iy0    # top

        # Bracket parameters.
        bracket_extent = 5
        bracket_color = [1.0, 1.0, 1.0, 1.0]

        # Handle tiles smaller than the bracket extent.
        bracket_width = min(bracket_extent, x1 - x0 + 1)
        bracket_height = min(bracket_extent, y1 - y0 + 1)

        # Top-left corner.
        self.__draw_hline(x0, y1, bracket_width, bracket_color)
        self.__draw_vline(x0, y1 - bracket_height + 1, bracket_height, bracket_color)

        # Top-right corner.
        self.__draw_hline(x1 - bracket_width + 1, y1, bracket_width, bracket_color)
        self.__draw_vline(x1, y1 - bracket_height + 1, bracket_height, bracket_color)

        # Bottom-left corner.
        self.__draw_hline(x0, y0, bracket_width, bracket_color)
        self.__draw_vline(x0, y0, bracket_height, bracket_color)

        # Bottom-right corner.
        self.__draw_hline(x1 - bracket_width + 1, y0, bracket_width, bracket_color)
        self.__draw_vline(x1, y0, bracket_height, bracket_color)

        return True

    def __draw_hline(self, x, y, length, color):
        result = self.begin_result(x, y, length, 1)
        layer = result.layers[0].passes[0]
        layer.rect = [color] * length
        self.end_result(result)

    def __draw_vline(self, x, y, length, color):
        result = self.begin_result(x, y, 1, length)
        layer = result.layers[0].passes[0]
        layer.rect = [color] * length
        self.end_result(result)
