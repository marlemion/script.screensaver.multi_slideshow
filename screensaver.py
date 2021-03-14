#!/usr/bin/python
# -*- coding: utf-8 -*-
#
#     Copyright (C) 2013 Tristan Fischer (sphere@dersphere.de)
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program. If not, see <http://www.gnu.org/licenses/>.
#

import random
import sys
import simplejson as json
from PIL import Image, ExifTags
from os import path, remove
import re
import threading
import time

import xbmc
import xbmcaddon
import xbmcvfs
from xbmcgui import ControlImage, ControlLabel, WindowDialog, Window, DialogProgress

addon = xbmcaddon.Addon()
ADDON_NAME = addon.getAddonInfo('name')
ADDON_PATH = addon.getAddonInfo('path')

MODES = (
    'TableDrop',
    'StarWars',
    'RandomZoomIn',
    'AppleTVLike',
    'GridSwitch',
    'SlidingPanels',
    'Random',
)
SOURCES = (
    'movies',
    'image_folder',
    'albums',
    'shows',
)
PROPS = (
    'fanart',
    'thumbnail',
)
CHUNK_WAIT_TIME = 250
ACTION_IDS_EXIT = [9, 10, 13, 92]


class ScreensaverManager(object):

    def __new__(cls):
        mode = MODES[int(addon.getSetting('mode'))]
        if mode == 'Random':
            subcls = random.choice(ScreensaverBase.__subclasses__())
            return subcls()
        for subcls in ScreensaverBase.__subclasses__():
            if subcls.MODE == mode:
                return subcls()
        raise ValueError('Not a valid ScreensaverBase subclass: %s' % mode)


class ExitMonitor(xbmc.Monitor):

    def __init__(self, exit_callback):
        self.exit_callback = exit_callback

    def onScreensaverDeactivated(self):
        self.exit_callback()


class ScreensaverWindow(WindowDialog):

    def __init__(self, exit_callback):
        self.exit_callback = exit_callback

    def onAction(self, action):
        action_id = action.getId()
        if action_id in ACTION_IDS_EXIT:
            self.exit_callback()


class Cache(threading.Thread): 

    def __init__(self, images): 
        threading.Thread.__init__(self) 
        self.pause = threading.Event()
        self.stop = threading.Event()
        self.idle = threading.Event()
        self.images = images
        self.rotated_pictures = []
        self.cache_cycle_image = cycle(self.images)


    def run(self):
        while True:
            time.sleep(0.05)
            if ( not self.pause.isSet() ):
                if ( len(screensaver.preload_controls) < screensaver.FAST_IMAGE_COUNT):
                    self.idle.clear()
                    image_url = next(self.cache_cycle_image)
                    self.preload_image(image_url)
                    self.idle.set()
            if ( self.stop.isSet() ):
                return


    def preload_image(self, image_url):
        # set the next image to an unvisible image-control for caching
        self.log('caching image: %s' % repr(image_url))
        image_url = self.rotate_image(image_url)
        screensaver.preload_controls[image_url] = ControlImage(-1, -1, 1, 1, image_url, False)
        screensaver.preload_controls[image_url].setVisible(False)
        screensaver.xbmc_window.addControl(screensaver.preload_controls[image_url])
        self.log('caching done')


    def rotate_image(self, image_url):

        source = SOURCES[int(addon.getSetting('source'))]

        # Do it only for real paths
        if ( source == 'image_folder' ):

            ROTATED = False
            
            image=Image.open(image_url)
        
            try:
                exif=dict(list(image._getexif().items()))
            except AttributeError:
                exif=[]
       
            try:            
                for orientation in list(ExifTags.TAGS.keys()):
                    if ExifTags.TAGS[orientation]=='Orientation':
                        break
                if exif[orientation] == 3:
                    image=image.rotate(180, expand=True)
                    ROTATED = True
                elif exif[orientation] == 6:
                    image=image.rotate(270, expand=True)
                    ROTATED = True
                elif exif[orientation] == 8:
                    image=image.rotate(90, expand=True)
                    ROTATED = True
            except ( IndexError, KeyError ):
                pass

            if ( ROTATED is True ):
                self.log('rotating image: %s' % repr(image_url))
                filepath = path.join(xbmc.translatePath("special://temp/"), path.split(image_url)[1])
                image.save(filepath)
                self.rotated_pictures.append(filepath)
                self.images[:] = [ x if x != image_url else filepath for x in self.images ]
                image_url = filepath

            image.close()
   
            try:            
                for datetimeoriginal in list(ExifTags.TAGS.keys()):
                    if ExifTags.TAGS[datetimeoriginal]=='DateTimeOriginal':
                        break
                #screensaver.image_dates[image_url] = exif[datetimeoriginal].encode('ascii', 'ignore')
                screensaver.image_dates[image_url] = exif[datetimeoriginal]
            except ( IndexError, KeyError ):
                screensaver.image_dates[image_url] = ''

            return image_url
            
        else:
            return image_url
   

    def delete_rotated_image(self, image_url):

        if ( image_url in self.rotated_pictures ):
            self.log('deleting image: %s' % repr(image_url))
            try:
                remove(image_url)
                self.log('image %s deleted' % repr(image_url))
            except OSError:
                self.log('error deleting image %s' % repr(image_url))


    def log(self, msg):
        xbmc.log('%s: Cache: %s' % (ADDON_NAME, msg))


class ScreensaverBase(object):

    MODE = None
    IMAGE_CONTROL_COUNT = 10
    FAST_IMAGE_COUNT = 10
    NEXT_IMAGE_TIME = 2000
    BORDER_WIDTH = 4
    BORDER_COLOR = 0
    BACKGROUND_IMAGE = 'black.jpg'
    RECTANGLES = 0
    RECTANGLES_ITER = 0
    EFFECT_SPEED = 1.0
    VIEW = 0
    CONTINUOUS = False

    def __init__(self):
        self.log('__init__ start')

        # Variables
        self.exit_requested = False
        self.background_control = None
        self.dialog = None
        self.recycle = False
        self.total_images = 0
        self.image_count = 0
        self.image_dates = {}

        # Controls
        self.image_controls = []
        self.global_controls = []
        self.border_controls = []
        self.black_label_controls = []
        self.white_label_controls = []
        self.top_image_controls = []
        self.custom_controls = {}
        self.preload_controls = {}

        # Init
        self.exit_monitor = ExitMonitor(self.stop)
        self.xbmc_window = ScreensaverWindow(self.stop)
        self.xbmc_window.show()
        self.init_global_controls()
        self.load_settings()
        self.init_cycle_controls()
        self.stack_cycle_controls()
        self.log('__init__ end')


    def init_global_controls(self):

        self.screen_width = Window().getWidth()
        self.screen_height = Window().getHeight()

        self.log('init_global_controls start')
        loading_img = xbmcvfs.validatePath('/'.join((
            ADDON_PATH, 'resources', 'media', 'loading.gif'
        )))
        self.background_control = ControlImage(0, 0, self.screen_width, self.screen_height, '')
        self.global_controls = [
            self.background_control
        ]
        self.xbmc_window.addControls(self.global_controls)
        self.log('init_global_controls end')


    def load_settings(self):
        pass


    def init_cycle_controls(self):
        self.log('init_cycle_controls start')
        for i in range(self.IMAGE_CONTROL_COUNT):
            img_control = ControlImage(0, 0, 0, 0, '', aspectRatio=1)
            self.image_controls.append(img_control)
        self.log('init_cycle_controls end')


    def stack_cycle_controls(self):
        self.log('stack_cycle_controls start')
        # add controls to the window in same order as image_controls list
        # so any new image will be in front of all previous images
        self.xbmc_window.addControls(self.image_controls)
        self.log('stack_cycle_controls end')


    def start_loop(self):
        self.log('start_loop start')

        # Get images from source
        images = self.get_images()

        # Shuffle images if requested
        if addon.getSetting('random_order') == 'true':
            random.shuffle(images)

        # Start the cacher with images
        self.cacher = Cache(images)
        self.cacher.start()

        # Define controls for the cycling and get first values
        image_url_cycle = cycle(images)
        image_controls_cycle = cycle(self.image_controls)

        image_url = next(image_url_cycle)
        image_control = next(image_controls_cycle)

        # Preload in case of first initating
        self.log('initial caching started')
        self.dialog.create('Caching images...', '' )
        while len(self.preload_controls) < self.FAST_IMAGE_COUNT:
            #time.sleep(10)
            time.sleep(0.01)
            self.dialog.update(int(100 * len(self.preload_controls) / self.FAST_IMAGE_COUNT), '    ')
        self.dialog.close()

        # Fade in the background
        self.show_background()

        # Do it for repetitive views only
        if ( self.VIEW == 1 ):
    
            # Let the cacher settle down
            self.cacher.pause.set()
            self.cacher.idle.wait()

            # Set the timing to fast values
            save_EFFECT_SPEED = self.EFFECT_SPEED
            save_NEXT_IMAGE_TIME = self.NEXT_IMAGE_TIME
            self.EFFECT_SPEED = 2.0
            self.NEXT_IMAGE_TIME = 10
            self.recycle = True
    
            # Now load the images in
            for image_url, control in list(self.preload_controls.items()):

               # Wait
               self.wait()

               if not self.exit_requested:
                   image_control = next(image_controls_cycle)
                   self.log('loading image: %s' % repr(image_url))
                   self.process_image(image_control, image_url)
                   self.image_count += 1
                   # Tidy up and move on
                   try:
                       self.cacher.delete_rotated_image(image_url)
                       self.xbmc_window.removeControl(self.preload_controls[image_url])
                       del self.preload_controls[image_url]
                   except KeyError:
                       pass
    
            self.cacher.pause.clear()
               
            # Reset the timing
            self.recycle = False
            self.EFFECT_SPEED = save_EFFECT_SPEED
            self.NEXT_IMAGE_TIME = save_NEXT_IMAGE_TIME
    
        # Do the loop
        while not self.exit_requested:

            # Wait
            self.wait()

            self.log('Count preload_controls ' + str(len(self.preload_controls)))
            self.log('Count image_controls ' + str(len(self.image_controls)))
            self.log('Count top_image_controls ' + str(len(self.top_image_controls)))
            self.log('Count border_controls ' + str(len(self.border_controls)))
            self.log('Count RECTANGLES ' + str(self.RECTANGLES))

            # Do it for a repetitively changing view
            if ( self.VIEW == 1 ):

                # Redraw the view in case we hit the repetitive condition
                try:
                    iter_check =  (self.image_count / self.RECTANGLES) % self.RECTANGLES_ITER
                except ZeroDivisionError:
                    iter_check = 0

                if ( ( self.image_count / self.RECTANGLES != 0 ) and iter_check == 0):

                    # Set the timing to fast values
                    self.recycle = True
                    save_EFFECT_SPEED = self.EFFECT_SPEED
                    save_NEXT_IMAGE_TIME = self.NEXT_IMAGE_TIME
                    self.EFFECT_SPEED = 2.0
                    self.NEXT_IMAGE_TIME = 10

                    # Set the current image controls to background color
                    self.log('setting image controls to background color')
                    for image_control in self.image_controls:
                        self.process_image(image_control, self.BORDER_COLOR)
                        image_control = next(image_controls_cycle)
                        # Tidy up and move on
                        try:
                            self.cacher.delete_rotated_image(image_url)
                            self.xbmc_window.removeControl(self.preload_controls[image_url])
                            del self.preload_controls[image_url]
                        except KeyError:
                            pass

                    # Remove extra controls if present
                    self.xbmc_window.removeControls(self.border_controls)
                    self.xbmc_window.removeControls(self.black_label_controls)
                    self.xbmc_window.removeControls(self.white_label_controls)
                    self.xbmc_window.removeControls(self.top_image_controls)
                    
                    # Do the actual redraw of the rectangle view
                    self.stack_cycle_controls()

                    # Now load the images in. But first ensure that we have
                    # enough images in the cache
                    while len(self.preload_controls) < self.FAST_IMAGE_COUNT:
                        time.sleep(0.05)
                    else:
                        # Prevent the cacher from distrubing the animations
                        self.cacher.pause.set()

                    # Wait for the cacher to settle down
                    self.cacher.idle.wait()

                    # Load the images from cache into the new view
                    cache_counter = 1
                    while cache_counter <= self.FAST_IMAGE_COUNT:

                        # Get the image_url and the image_control
                        for image_url, control in list(self.preload_controls.items()):
                            break
                        image_control = next(image_controls_cycle)
                        self.log('loading image: %s' % repr(image_url))
                        self.process_image(image_control, image_url)
                        self.image_count += 1
                        cache_counter += 1

                        # Tidy up and move on
                        #try:
                        self.cacher.delete_rotated_image(image_url)
                        self.xbmc_window.removeControl(self.preload_controls[image_url])
                        del self.preload_controls[image_url]
                        #except KeyError:
                        #    pass

                    # Let the cache do its work again
                    self.cacher.pause.clear()

                    # Reset the timing
                    self.recycle = False
                    self.EFFECT_SPEED = save_EFFECT_SPEED
                    self.NEXT_IMAGE_TIME = save_NEXT_IMAGE_TIME
                    
                # Fill up cache
                while len(self.preload_controls) <= 2:
                    time.sleep(0.05)

                # Get the image_url and the image_control
                for image_url, control in list(self.preload_controls.items()):
                    break

                image_control = next(image_controls_cycle)
   
                if ( self.CONTINUOUS is False ):
                    # Disable caching
                    self.cacher.pause.set()
                    # Let the cacher settle down
                    self.cacher.idle.wait()
                # Do the animation
                self.log('using image: %s' % repr(image_url))
                self.process_image(image_control, image_url)

                # Tidy up and move on
                #try:
                self.cacher.delete_rotated_image(image_url)
                self.xbmc_window.removeControl(self.preload_controls[image_url])
                del self.preload_controls[image_url]
                #except KeyError:
                #    pass

                if ( self.CONTINUOUS is False ):
                    # Enable caching
                    self.cacher.pause.clear()
                self.image_count += 1

            # Do the normal work
            else:

                # Fill up cache
                while len(self.preload_controls) <= 2:
                    #xbmc.sleep(50)
                    time.sleep(0.05)

                # Get the image_url from the cache
                for image_url, control in list(self.preload_controls.items()):
                    break

                image_control = next(image_controls_cycle)

                if ( self.CONTINUOUS is False ):
                    # Disable caching
                    self.cacher.pause.set()
                    # Let the cacher settle down
                    self.cacher.idle.wait()
                # Do the animation
                self.log('using image: %s' % repr(image_url))
                self.process_image(image_control, image_url)

                # Tidy up and move on
                try:
                    self.cacher.delete_rotated_image(image_url)
                    self.xbmc_window.removeControl(self.preload_controls[image_url])
                    del self.preload_controls[image_url]
                except KeyError:
                    pass

                if ( self.CONTINUOUS is False ):
                    # Enable caching
                    self.cacher.pause.clear()
                self.image_count += 1


    def get_images(self):

        self.image_aspect_ratio = 16.0 / 9.0
        source = SOURCES[int(addon.getSetting('source'))]
        prop = PROPS[int(addon.getSetting('prop'))]
        self.dialog = DialogProgress()
        images = []
        if source == 'movies':
            images = self._get_json_images('VideoLibrary.GetMovies', 'movies', prop)
        elif source == 'albums':
            images = self._get_json_images('AudioLibrary.GetAlbums', 'albums', prop)
        elif source == 'shows':
            images = self._get_json_images('VideoLibrary.GetTVShows', 'tvshows', prop)
        elif source == 'image_folder':
            path = addon.getSetting('image_path')
            self.log(path)
            if path:
                self.dialog.create('Getting images recursively')
                images = self._get_folder_images(path)
                self.dialog.close()
        if not images:
            cmd = 'XBMC.Notification("{header}", "{message}")'.format(
                header=addon.getLocalizedString(32500),
                message=addon.getLocalizedString(32501)
            )
            xbmc.executebuiltin(cmd)
            images = (
                self._get_json_images('VideoLibrary.GetMovies', 'movies', 'fanart')
                or self._get_json_images('AudioLibrary.GetArtists', 'artists', 'fanart')
            )
        return images


    def _get_json_images(self, method, key, prop):
        self.log('_get_json_images start')
        query = {
            'jsonrpc': '2.0',
            'id': 0,
            'method': method,
            'params': {
                'properties': [prop],
            }
        }
        response = json.loads(xbmc.executeJSONRPC(json.dumps(query)))
        images = [
            element[prop] for element
            in response.get('result', {}).get(key, [])
            if element.get(prop)
        ]

        self.log('_get_json_images end')
        return images


    def _get_folder_dirs(self, dirs, path):

        directories, files = xbmcvfs.listdir(path)

        for directory in directories:
            dirs.append(xbmcvfs.validatePath('/'.join((path, directory, ''))))

        if addon.getSetting('recursive') == 'true':
            for directory in directories:
                if directory.startswith('.'):
                    continue
                for sub_directory in self._get_folder_dirs([], xbmcvfs.validatePath('/'.join((path, directory, '')))):
                    dirs.append(xbmcvfs.validatePath(sub_directory))
        
        return dirs


    def _get_folder_images(self, path):

        self.log('_get_folder_images started')
        def _dive_into_dir(path):

            directories, files = xbmcvfs.listdir(path)

            images = [
                xbmcvfs.validatePath(path + f) for f in files
                if f.lower()[-3:] in ('jpg', 'png', 'bmp')
            ]

            return images

        dirs = self._get_folder_dirs([], path)
        dir_count = 1

        images = []
        for directory in dirs:
            progress_update = int(100 * dir_count / len(dirs))
            self.dialog.update(progress_update, directory)
            dir_count = dir_count + 1
            images.extend(_dive_into_dir(directory))

        return images


    def show_background(self):
        bg_img = xbmcvfs.validatePath('/'.join((
            ADDON_PATH, 'resources', 'media', self.BACKGROUND_IMAGE
        )))
        self.background_control.setAnimations([(
            'conditional',
            'effect=fade start=0 end=100 time=500 delay=500 condition=true'
        )])
        self.background_control.setImage(bg_img)


    def process_image(self, image_control, image_url):
        # Needs to be implemented in sub class
        raise NotImplementedError


    def wait(self):
        # wait in chunks of 500ms to react earlier on exit request
        chunk_wait_time = int(CHUNK_WAIT_TIME)
        remaining_wait_time = int(self.NEXT_IMAGE_TIME)
        while remaining_wait_time > 0:
            if self.exit_requested:
                self.cacher.stop.set()
                return
            if remaining_wait_time < chunk_wait_time:
                chunk_wait_time = remaining_wait_time
            remaining_wait_time -= chunk_wait_time
            #time.sleep(float(chunk_wait_time) / 1000)
            xbmc.sleep(chunk_wait_time)


    def stop(self):
        self.log('stop')
        self.exit_requested = True
        self.exit_monitor = None


    def close(self):
        self.del_controls()


    def del_controls(self):
        self.log('del_controls start')
        self.xbmc_window.removeControls(self.image_controls)
        self.xbmc_window.removeControls(self.global_controls)
        #self.xbmc_window.removeControls(self.preload_controls)
        self.xbmc_window.removeControls(self.top_image_controls)
        self.xbmc_window.removeControls(self.black_label_controls)
        self.xbmc_window.removeControls(self.white_label_controls)
        self.preload_controls = {}
        self.custom_controls = {}
        self.background_control = None
        self.image_dates = []
        self.image_controls = []
        self.global_controls = []
        self.black_label_controls = []
        self.white_label_controls = []
        self.top_image_controls = []
        self.xbmc_window.close()
        self.xbmc_window = None
        self.log('del_controls end')

    def log(self, msg):
        xbmc.log('%s: %s' % (ADDON_NAME, msg))


class TableDropScreensaver(ScreensaverBase):

    MODE = 'TableDrop'
    BACKGROUND_IMAGE = 'table.jpg'
    IMAGE_CONTROL_COUNT = 20
    FAST_IMAGE_COUNT = 10
    NEXT_IMAGE_TIME = 1500
    MIN_WIDTH = 500
    MAX_WIDTH = 700

    def load_settings(self):
        self.NEXT_IMAGE_TIME = int(addon.getSetting('tabledrop_wait'))

    def process_image(self, image_control, image_url):
        ROTATE_ANIMATION = (
            'effect=rotate start=0 end=%d center=auto time=%d '
            'delay=0 tween=circle condition=true'
        )
        DROP_ANIMATION = (
            'effect=zoom start=%d end=100 center=auto time=%d '
            'delay=0 tween=circle condition=true'
        )
        FADE_ANIMATION = (
            'effect=fade start=0 end=100 time=200 '
            'condition=true'
        )
        # hide the image
        image_control.setVisible(False)
        image_control.setImage('')
        # re-stack it (to be on top)
        self.xbmc_window.removeControl(image_control)
        self.xbmc_window.addControl(image_control)
        # calculate all parameters and properties
        width = random.randint(self.MIN_WIDTH, self.MAX_WIDTH)
        height = int(width / self.image_aspect_ratio)
        x_position = random.randint(0, self.screen_width - width)
        y_position = random.randint(0, self.screen_height - height)
        drop_height = random.randint(400, 800)
        drop_duration = drop_height * 1.5
        rotation_degrees = random.uniform(-20, 20)
        rotation_duration = drop_duration
        animations = [
            ('conditional', FADE_ANIMATION),
            ('conditional',
             ROTATE_ANIMATION % (rotation_degrees, rotation_duration)),
            ('conditional',
             DROP_ANIMATION % (drop_height, drop_duration)),
        ]
        # set all parameters and properties
        image_control.setImage(image_url)
        image_control.setPosition(x_position, y_position)
        image_control.setWidth(width)
        image_control.setHeight(height)
        image_control.setAnimations(animations)
        # show the image
        image_control.setVisible(True)
        #xbmc.sleep(int(drop_duration))
        time.sleep(float(drop_duration) / 1000)


class StarWarsScreensaver(ScreensaverBase):

    MODE = 'StarWars'
    BACKGROUND_IMAGE = 'stars.jpg'
    IMAGE_CONTROL_COUNT = 6
    FAST_IMAGE_COUNT = 6
    SPEED = 0.5
    CONTINUOUS = True

    def load_settings(self):
        self.SPEED = float(addon.getSetting('starwars_speed'))
        self.EFFECT_TIME = 9000.0 / self.SPEED
        self.NEXT_IMAGE_TIME = self.EFFECT_TIME / 7.6

    def process_image(self, image_control, image_url):
        TILT_ANIMATION = (
            'effect=rotatex start=0 end=55 center=auto time=0 '
            'condition=true'
        )
        MOVE_ANIMATION = (
            'effect=slide start=0,self.screen_width end=0,-2560 time=%d '
            'tween=linear condition=true'
        )
        # hide the image
        image_control.setImage('')
        image_control.setVisible(False)
        # re-stack it (to be on top)
        self.xbmc_window.removeControl(image_control)
        self.xbmc_window.addControl(image_control)
        # calculate all parameters and properties
        width = self.screen_width
        height = self.screen_height
        x_position = 0
        y_position = 0
        animations = [
            ('conditional', TILT_ANIMATION),
            ('conditional', MOVE_ANIMATION % self.EFFECT_TIME),
        ]
        # set all parameters and properties
        image_control.setPosition(x_position, y_position)
        image_control.setWidth(width)
        image_control.setHeight(height)
        image_control.setAnimations(animations)
        image_control.setImage(image_url)
        # show the image
        image_control.setVisible(True)


class RandomZoomInScreensaver(ScreensaverBase):

    MODE = 'RandomZoomIn'
    IMAGE_CONTROL_COUNT = 7
    FAST_IMAGE_COUNT = 7
    NEXT_IMAGE_TIME = 2000
    EFFECT_TIME = 5000

    def load_settings(self):
        self.NEXT_IMAGE_TIME = int(addon.getSetting('randomzoom_wait'))
        self.EFFECT_TIME = int(addon.getSetting('randomzoom_effect'))

    def process_image(self, image_control, image_url):
        ZOOM_ANIMATION = (
            'effect=zoom start=1 end=100 center=%d,%d time=%d '
            'tween=quadratic condition=true'
        )
        # hide the image
        image_control.setVisible(False)
        image_control.setImage('')
        # re-stack it (to be on top)
        self.xbmc_window.removeControl(image_control)
        self.xbmc_window.addControl(image_control)
        # calculate all parameters and properties
        width = self.screen_width
        height = self.screen_height
        x_position = 0
        y_position = 0
        zoom_x = random.randint(0, self.screen_width)
        zoom_y = random.randint(0, self.screen_height)
        animations = [
            ('conditional', ZOOM_ANIMATION % (zoom_x, zoom_y, self.EFFECT_TIME)),
        ]
        # set all parameters and properties
        image_control.setImage(image_url)
        image_control.setPosition(x_position, y_position)
        image_control.setWidth(width)
        image_control.setHeight(height)
        image_control.setAnimations(animations)
        # show the image
        image_control.setVisible(True)


class AppleTVLikeScreensaver(ScreensaverBase):

    MODE = 'AppleTVLike'
    IMAGE_CONTROL_COUNT = 35
    FAST_IMAGE_COUNT = 10
    DISTANCE_RATIO = 0.7
    SPEED = 1.0
    CONCURRENCY = 1.0

    def load_settings(self):
        self.SPEED = float(addon.getSetting('appletvlike_speed'))
        self.CONCURRENCY = float(addon.getSetting('appletvlike_concurrency'))
        self.MAX_TIME = int(15000 / self.SPEED)
        self.NEXT_IMAGE_TIME = int(4500.0 / self.CONCURRENCY / self.SPEED)

    def stack_cycle_controls(self):
        # randomly generate a zoom in percent as betavariant
        # between 10 and 70 and assign calculated width to control.
        # Remove all controls from window and re-add sorted by size.
        # This is needed because the bigger (=nearer) ones need to be in front
        # of the smaller ones.
        # Then shuffle image list again to have random size order.

        for image_control in self.image_controls:
            zoom = int(random.betavariate(2, 2) * 40) + 10
            #zoom = int(random.randint(10, 70))
            width = int(self.screen_width / 100 * zoom)
            image_control.setWidth(int(width))
        self.image_controls = sorted(
            self.image_controls, key=lambda c: c.getWidth()
        )
        self.xbmc_window.addControls(self.image_controls)
        random.shuffle(self.image_controls)

    def process_image(self, image_control, image_url):
        MOVE_ANIMATION = (
            'effect=slide start=0,self.screen_height end=0,-self.screen_height center=auto time=%s '
            'tween=linear delay=0 condition=true'
        )
        image_control.setVisible(False)
        image_control.setImage('')
        # calculate all parameters and properties based on the already set
        # width. We can not change the size again because all controls need
        # to be added to the window in size order.
        width = image_control.getWidth()
        zoom = width * 100 / self.screen_width
        height = int(width / self.image_aspect_ratio)
        # let images overlap max 1/2w left or right
        center = random.randint(0, self.screen_width)
        x_position = int(center - width / 2)
        y_position = 0

        time = self.MAX_TIME / zoom * self.DISTANCE_RATIO * 100

        animations = [
            ('conditional', MOVE_ANIMATION % time),
        ]
        # set all parameters and properties
        image_control.setImage(image_url)
        image_control.setPosition(x_position, y_position)
        image_control.setWidth(width)
        image_control.setHeight(height)
        image_control.setAnimations(animations)
        # show the image
        image_control.setVisible(True)


class GridSwitchScreensaver(ScreensaverBase):

    MODE = 'GridSwitch'

    ROWS_AND_COLUMNS = 4
    RECTANGLES = 16
    NEXT_IMAGE_TIME = 1000
    EFFECT_TIME = 500
    RANDOM_ORDER = False
    VIEW = 0

    IMAGE_CONTROL_COUNT = ROWS_AND_COLUMNS ** 2
    FAST_IMAGE_COUNT = IMAGE_CONTROL_COUNT

    def load_settings(self):
        self.NEXT_IMAGE_TIME = int(addon.getSetting('gridswitch_wait'))
        self.ROWS_AND_COLUMNS = int(addon.getSetting('gridswitch_rows_columns'))
        self.RANDOM_ORDER = addon.getSetting('gridswitch_random') == 'true'
        self.IMAGE_CONTROL_COUNT = self.ROWS_AND_COLUMNS ** 2
        self.RECTANGLES = self.ROWS_AND_COLUMNS * self.ROWS_AND_COLUMNS
        self.FAST_IMAGE_COUNT = self.IMAGE_CONTROL_COUNT

    def stack_cycle_controls(self):
        # Set position and dimensions based on stack position.
        # Shuffle image list to have random order.
        super(GridSwitchScreensaver, self).stack_cycle_controls()
        for i, image_control in enumerate(self.image_controls):
            current_row, current_col = divmod(i, self.ROWS_AND_COLUMNS)
            width = int(self.screen_width / self.ROWS_AND_COLUMNS)
            height = int(self.screen_height / self.ROWS_AND_COLUMNS)
            x_position = int(width * current_col)
            y_position = int(height * current_row)
            image_control.setPosition(x_position, y_position)
            image_control.setWidth(width)
            image_control.setHeight(height)
        if self.RANDOM_ORDER:
            random.shuffle(self.image_controls)

    def process_image(self, image_control, image_url):
        if not self.image_count < self.FAST_IMAGE_COUNT:
            FADE_OUT_ANIMATION = (
                'effect=fade start=100 end=0 time=%d condition=true' % self.EFFECT_TIME
            )
            animations = [
                ('conditional', FADE_OUT_ANIMATION),
            ]
            image_control.setAnimations(animations)
            #xbmc.sleep(self.EFFECT_TIME)
            time.sleep(float(self.EFFECT_TIME) / 1000)
        image_control.setImage(image_url)
        FADE_IN_ANIMATION = (
            'effect=fade start=0 end=100 time=%d condition=true' % self.EFFECT_TIME
        )
        animations = [
            ('conditional', FADE_IN_ANIMATION),
        ]
        image_control.setAnimations(animations)


class SlidingPanelsScreensaver(ScreensaverBase):

    MODE = 'SlidingPanels'

    ROWS_AND_COLUMNS = 4
    NEXT_IMAGE_TIME = 1000
    EFFECT_SPEED = 0.5
    VIEW = 1
    RECTANGLES = 5
    RANDOM_ORDER = False
    DESCRIPTION = False
    BORDER = True
    BORDER_WIDTH = 4
    BORDER_COLOR = 0
    BACKGROUND_IMAGE="black.jpg"

    IMAGE_CONTROL_COUNT = RECTANGLES
    FAST_IMAGE_COUNT = IMAGE_CONTROL_COUNT

    def load_settings(self):
        self.VIEW = int(addon.getSetting('slidingpanels_mode'))
        self.ROWS_AND_COLUMNS = int(addon.getSetting('slidingpanels_rows_columns'))
        self.RECTANGLES = int(addon.getSetting('slidingpanels_random_rectangles'))
        self.RECTANGLES_ITER = int(addon.getSetting('slidingpanels_random_iteration'))
        self.NEXT_IMAGE_TIME = int(addon.getSetting('slidingpanels_wait'))
        self.EFFECT_SPEED = float(addon.getSetting('slidingpanels_speed'))
        self.RANDOM_ORDER = addon.getSetting('slidingpanels_random') == 'true'
        self.DESCRIPTION = addon.getSetting('slidingpanels_description') == 'true'
        self.DESCRIPTION_POSITION = int(addon.getSetting('slidingpanels_description_position'))
        self.BORDER = addon.getSetting('slidingpanels_border') == 'true'
        self.BORDER_WIDTH = int(addon.getSetting('slidingpanels_border_width'))
        self.BORDER_COLOR = int(addon.getSetting('slidingpanels_border_color'))

        if ( self.VIEW == 1 ):
            self.IMAGE_CONTROL_COUNT = self.RECTANGLES
        else:
            self.IMAGE_CONTROL_COUNT = self.ROWS_AND_COLUMNS ** 2
        self.FAST_IMAGE_COUNT = self.IMAGE_CONTROL_COUNT

        if ( self.DESCRIPTION_POSITION == 0 ):
            self.DESCRIPTION_Y = 'top'
            self.DESCRIPTION_X = 0
        elif ( self.DESCRIPTION_POSITION == 1 ):
            self.DESCRIPTION_Y = 'top'
            self.DESCRIPTION_X = 2
        elif ( self.DESCRIPTION_POSITION == 2 ):
            self.DESCRIPTION_Y = 'top'
            self.DESCRIPTION_X = 1
        elif ( self.DESCRIPTION_POSITION == 3 ):
            self.DESCRIPTION_Y = 'bottom'
            self.DESCRIPTION_X = 0
        elif ( self.DESCRIPTION_POSITION == 4 ):
            self.DESCRIPTION_Y = 'bottom'
            self.DESCRIPTION_X = 2
        elif ( self.DESCRIPTION_POSITION == 5 ):
            self.DESCRIPTION_Y = 'bottom'
            self.DESCRIPTION_X = 1

        if ( self.BORDER_COLOR == 0 ):
            self.BORDER_COLOR = xbmcvfs.validatePath('/'.join((
                ADDON_PATH, 'resources', 'media', 'black.jpg'
            )))
            self.BACKGROUND_IMAGE="black.jpg"
        elif ( self.BORDER_COLOR == 1 ):
            self.BORDER_COLOR = xbmcvfs.validatePath('/'.join((
                ADDON_PATH, 'resources', 'media', 'white.jpg'
            )))
            self.BACKGROUND_IMAGE="white.jpg"


    def stack_cycle_controls(self):
        # Set position and dimensions based on stack position.
        # Shuffle image list to have random order.

        if ( self.recycle is False ):
            super(SlidingPanelsScreensaver, self).stack_cycle_controls()

        class Rectangle:
        
            def __init__(self,x, y, w, h):
                self.x = x
                self.y = y
                self.h = h
                self.w = w
                self.position = (self.x,self.y)
                self.area = h * w
        
        
            def random_divide(self, orientation=None):
       
                if ( self.w < self.h ):
                    orientation = self.h
                elif ( self.w > self.h ):
                    orientation = self.w
                else:
                    orientation = self.w

                division = random.choice([ 2, 3, 4 ])
                first_image_smaller = random.choice([ True, False ])
        
                new = int( orientation / division )
        
                if ( orientation == self.w ):
                    if ( first_image_smaller is True ):
                        return [ Rectangle(self.x, self.y, new, self.h), Rectangle(self.x + new, self.y, self.w - new, self.h) ]
                    else:
                        return [ Rectangle(self.x, self.y, self.w - new, self.h), Rectangle(self.x + (self.w - new ), self.y, new, self.h) ]
                else:
                    if ( first_image_smaller is True ):
                        return [ Rectangle(self.x, self.y, self.w, new), Rectangle(self.x, self.y + new, self.w, self.h - new) ]
                    else:
                        return [ Rectangle(self.x, self.y, self.w, self.h - new), Rectangle(self.x, self.y + ( self.h - new ), self.w, new) ]
        
        
        def make_rects(w, h, number):
        
            rectangles = [ Rectangle(0, 0, w, h) ]
            w_threshold = w / 4
            h_threshold = h / 4
        
            # Choose the rectangle with the highest area
            rectangles.sort(key=lambda x: x.area, reverse=True)
        
            while ( len(rectangles) < number ):

                rectangles.sort(key=lambda x: x.area, reverse=True)
                rectangle = rectangles[0]
                rectangles = rectangles + rectangle.random_divide()
                rectangles.remove(rectangle)

            return rectangles


        if ( self.BORDER is True ):
            border = self.BORDER_WIDTH
        else:
            border = 0
        half_border = int(border / 2)
        onehalf_border = border + half_border

        # Create random retangles
        if ( self.VIEW == 1 ):
            random_rectangles = make_rects(self.screen_width, self.screen_height, self.RECTANGLES)

        # Cycle through image controls and set dimensions and position
        for i, image_control in enumerate(self.image_controls):

            if ( self.VIEW == 0 ):
                current_row, current_col = divmod(i, self.ROWS_AND_COLUMNS)
    
                orig_width = self.screen_width / self.ROWS_AND_COLUMNS
                orig_height = self.screen_height / self.ROWS_AND_COLUMNS
                orig_x_position = ( orig_width * current_col )
                orig_y_position = ( orig_height * current_row )

                # Rule out images, which are directly at the border of
                # the screen and set the border respectively, if necessary
                if ( current_col == 0 ):
                    width = orig_width - onehalf_border
                    x_position = ( orig_x_position ) + border
                elif ( current_col == self.ROWS_AND_COLUMNS - 1 ):
                    width = orig_width - onehalf_border
                    x_position = ( orig_x_position ) + half_border
                else:
                    width = orig_width - border
                    x_position = ( orig_x_position ) + half_border
    
                if ( current_row == 0 ):
                    height = orig_height - onehalf_border
                    y_position = ( orig_y_position ) + border
                elif ( current_row == self.ROWS_AND_COLUMNS - 1 ):
                    height = orig_height - onehalf_border
                    y_position = ( orig_y_position ) + half_border
                else:
                    height = orig_height - border 
                    y_position = ( orig_y_position ) +  half_border

            elif (self.VIEW == 1 ):

                rectangle = random_rectangles[i]
                orig_height = rectangle.h
                orig_width = rectangle.w
                orig_y_position = rectangle.y
                orig_x_position = rectangle.x

                if ( orig_x_position == 0 ) and ( orig_width + orig_x_position == self.screen_width):
                    width = orig_width - 2 * border
                    x_position = orig_x_position + border
                if ( orig_x_position == 0 ):
                    width = orig_width - onehalf_border
                    x_position = orig_x_position + border
                elif ( orig_width + orig_x_position == self.screen_width):
                    width = orig_width - onehalf_border
                    x_position = orig_x_position + half_border
                else:
                    width = orig_width - border
                    x_position = orig_x_position + half_border

                if ( orig_y_position == 0 ) and ( orig_height + orig_y_position == self.screen_height):
                    height = orig_height - 2 * border
                    y_position = orig_y_position + border
                elif ( orig_y_position == 0 ):
                    height = orig_height - onehalf_border
                    y_position = orig_y_position + border
                elif ( orig_height + orig_y_position == self.screen_height):
                    height = orig_height - onehalf_border
                    y_position = orig_y_position + half_border
                else:
                    height = orig_height - border
                    y_position = orig_y_position + half_border
   
            # Move to int
            orig_y_position = int(orig_y_position)
            orig_x_position = int(orig_x_position)
            y_position = int(y_position)
            x_position = int(x_position)
            orig_height = int(orig_height)
            orig_width = int(orig_width)
            height = int(height)
            width = int(width)

            # Set the dimensions of the image control for sliding the panels
            image_control.setPosition(x_position, y_position)
            image_control.setWidth(width)
            image_control.setHeight(height)
            image_control.setVisible(False)

            custom_controls = {}

            # Set the dimension of the image control being on top (to hide sliding panels)
            custom_controls['top_image_control'] = ControlImage(x_position, y_position, width, height, '', aspectRatio=1)

            # Set the dimensions of the image control for the border (must be on top)
            border_controls = {}

            if ( self.BORDER is True ):
                border_controls['top'] = ControlImage(x_position - border, y_position - border, orig_width, border, '')
                border_controls['left'] = ControlImage(x_position - border, y_position - border, border, orig_height, '')
                border_controls['right'] = ControlImage(x_position + width, orig_y_position, border, orig_height, '')
                border_controls['bottom'] = ControlImage(x_position - border, y_position + height, orig_width, border, '')

            custom_controls['border_controls'] = border_controls
           
            # Set the labels
            black_label_controls = {}
            white_label_controls = {}

            if ( self.DESCRIPTION is True ):
                # Unfortunately, KODI does not support outlined fonts, which are nice to have on bright pictures.
                # Hence, we create four underlying imagecontrols with black color and one on top with white color
                if (self.DESCRIPTION_Y == 'top'):
                    description_x = x_position + 10
                    description_y = y_position + 10
                elif (self.DESCRIPTION_Y == 'bottom'):
                    description_x = x_position + 10
                    description_y = y_position + height - 40
                black_label_controls['b_left'] = ControlLabel(description_x - 1, description_y, width - 20, 30, '', textColor='0xFF000000', alignment=self.DESCRIPTION_X)
                black_label_controls['b_right'] = ControlLabel(description_x + 1, description_y, width - 20, 30, '', textColor='0xFF000000', alignment=self.DESCRIPTION_X)
                black_label_controls['b_top'] = ControlLabel(description_x, description_y + 1, width - 20, 30, '', textColor='0xFF000000', alignment=self.DESCRIPTION_X)
                black_label_controls['b_bottom'] = ControlLabel(description_x, description_y - 1, width - 20, 30, '', textColor='0xFF000000', alignment=self.DESCRIPTION_X)
                white_label_controls['w_center'] = ControlLabel(description_x, description_y, width - 20, 30, '', textColor='0xFFFFFFFF', alignment=self.DESCRIPTION_X)

            # Add label controls to custom controls
            custom_controls['black_label_controls'] = black_label_controls
            custom_controls['white_label_controls'] = white_label_controls

            # Add all controls to the dict with the ID of the image_control as key
            self.custom_controls[image_control.getId()] = custom_controls

        # Activate the custom controls in the appropriate order:
        # All custom controls are on top of the image_controls
        # Highest up are the borders
        # Below are the labels
        # Below the labes are the top images
        self.border_controls = [ i for l in [ [ l_ctrl for l, l_ctrl in l_lists ] for l_lists in [ list(ctrls['border_controls'].items()) for k, ctrls in list(self.custom_controls.items()) ] ] for i in l ]
        self.black_label_controls = [ i for l in [ [ l_ctrl for l, l_ctrl in l_lists ] for l_lists in [ list(ctrls['black_label_controls'].items()) for k, ctrls in list(self.custom_controls.items()) ] ] for i in l ]
        self.white_label_controls = [ i for l in [ [ l_ctrl for l, l_ctrl in l_lists ] for l_lists in [ list(ctrls['white_label_controls'].items()) for k, ctrls in list(self.custom_controls.items()) ] ] for i in l ]
        self.top_image_controls = [ ctrls['top_image_control'] for k, ctrls in list(self.custom_controls.items()) ]

        # Add top image controls
        self.xbmc_window.addControls(self.top_image_controls)

        # Add black labels
        self.xbmc_window.addControls(self.black_label_controls)

        # Add white labels
        self.xbmc_window.addControls(self.white_label_controls)

        # Add borders
        self.xbmc_window.addControls(self.border_controls)

        if self.RANDOM_ORDER:
            random.shuffle(self.image_controls)


    def process_image(self, image_control, image_url):

        # Get random slide in variables
        horizontal_in = bool(random.getrandbits(1))
        leftup_in = bool(random.getrandbits(1))
        leftup_out = bool(random.getrandbits(1))

        # Get the control labels
        custom_controls = self.custom_controls[image_control.getId()]
        top_image_control = custom_controls['top_image_control']
        border_controls = custom_controls['border_controls']
        black_label_controls = [ label_controls for key, label_controls in list(custom_controls['black_label_controls'].items()) ]
        white_label_controls = [ label_controls for key, label_controls in list(custom_controls['white_label_controls'].items()) ]

        # If the status of visibility is False, we entered the image_control the first time
        if ( image_control.isVisible() ):
            initiating = False
        else:
            initiating = True
            image_control.setVisible(True)
            for key, border_control in list(border_controls.items()):
                border_control.setImage(self.BORDER_COLOR)

            # Set the timing to fast values
            save_EFFECT_SPEED = self.EFFECT_SPEED
            save_NEXT_IMAGE_TIME = self.NEXT_IMAGE_TIME
            self.EFFECT_SPEED = 2.0
            self.NEXT_IMAGE_TIME = 10
            self.recycle = True

        # Remove the control labels ( image gets replaced)
        if ( self.DESCRIPTION is True ) or ( self.recycle is True ):
            for label_control in black_label_controls + white_label_controls:
                label_control.setVisible(False)

        # Remove the top image (we would like to see the sliding animation)
        top_image_control.setVisible(False)

        # Based on the randomly determined slide in variables
        # build the start and end points of the MOVE_ANIMATIONS
        width = str(image_control.getWidth())
        height = str(image_control.getHeight())

        if ( horizontal_in is True ) and ( leftup_out is True ):
            if ( leftup_in is True ):
                start = '0,-' + height  # incoming, end = 0,0
            else:
                start = '0,' + height  # incoming, end = 0,0
            end = '-' + width + ',0' # outgoing, start = 0,0
        elif ( horizontal_in is True ) and ( leftup_out is False ):
            if ( leftup_in is True ):
                start = '0,-' + height  # incoming, end = 0,0
            else:
                start = '0,' + height  # incoming, end = 0,0
            end = width + ',0' # outgoing, start = 0,0
        elif ( horizontal_in is False ) and ( leftup_out is True ):
            if ( leftup_in is True ):
                start = '-' + width + ',0'  # incoming, end = 0,0
            else:
                start = width + ',0'  # incoming, end = 0,0
            end = '0,-' + height # outgoing, start = 0,0
        elif ( horizontal_in is False ) and ( leftup_out is False ):
            if ( leftup_in is True ):
                start = '-' + width + ',0'  # incoming, end = 0,0
            else:
                start = width + ',0'  # incoming, end = 0,0
            end = '0,' + height # outgoing, start = 0,0
    
        # Determine the timing based on the size of the image
        if ( horizontal_in is True ):
            if ( initiating is False ):
                time_in = int(float(height) / self.EFFECT_SPEED )
            else:
                time_in = 500
            if ( self.recycle is False ):
                time_out = int(float(width) / self.EFFECT_SPEED )
            else:
                time_out = 10
        else:
            if ( initiating is False ):
                time_in = int(float(width) / self.EFFECT_SPEED )
            else:
                time_in = 500
            if ( self.recycle is False ):
                time_out = int(float(height) / self.EFFECT_SPEED )
            else:
                time_out = 10

        # Slide out
        MOVE_ANIMATION = (
           'effect=slide start=0,0 end=%s time=%s '
            'tween=sine ease=in delay=10 condition=true'
        )
        animations = [
            ('conditional', MOVE_ANIMATION % ( end, time_out ) )
        ]
        image_control.setAnimations(animations)

        if self.recycle is False:
            #xbmc.sleep(time_out + 10)
            time.sleep( float(time_out + 10) / 1000)

        # Prepare for slide in
        image_control.setImage(image_url)
        top_image_control.setImage(image_url)
        # Adapt image name
        if ( self.DESCRIPTION is True ):
            if ( image_url != self.BORDER_COLOR ):
                #image_name = image_url.split('/')[-1].split('.')[0].replace('_', ' ')
                #image_name = ''.join([i for i in image_name if not i.isdigit()])
                image_name = path.splitext(path.split(image_url)[1])[0]
                image_name = re.sub('_(\s)?[0-9]*$', '', image_name)
                image_name = re.sub('^[0-9]*(\s)?_', '', image_name)
                image_name = re.sub('-(\s)?[0-9]*$', '', image_name)
                image_name = re.sub('^[0-9]*(\s)?-', '', image_name)
                image_name = image_name.replace('_', ' ').strip()
                try:
                    year = self.image_dates[image_url].split(':')[0]
                except KeyError:
                    year = ''
                if ( year != '' ):
                    image_name = image_name + ' (' + year + ')'
            else:
                image_name = ''
            for label_control in black_label_controls + white_label_controls:
                label_control.setLabel(image_name)

        # If we initiate, we would rather zoom the images in
        if ( initiating is True ):
            MOVE_ANIMATION = (
                'effect=zoom start=10 end=100 time=%s '
                'tween=bounce ease=in delay=50 center=auto condition=true'
            )
            animations = [
                ('conditional', MOVE_ANIMATION % time_in)
            ]
            image_control.setAnimations(animations)
            image_control.setVisible(True)
        else:
            MOVE_ANIMATION = (
                'effect=slide start=%s end=0,0 time=%s '
                'tween=sine ease=out delay=10 condition=true'
            )
            animations = [
                ('conditional', MOVE_ANIMATION % ( start, time_in) )
            ]
            image_control.setAnimations(animations)

        if ( self.DESCRIPTION is True ):
            FADE_ANIMATION = (
                'effect=fade start=0 end=100 time=%s '
                'tween=sine ease=out delay=10 condition=true'
            )
            animations = [
                ('conditional', FADE_ANIMATION % time_in )
            ]

            for label_control in black_label_controls + white_label_controls:
                label_control.setAnimations(animations)

            # Adjust the time of the appearance of the description based on
            # position and slide in animation
            if ( self.recycle is False ):
                # If we slide in from below or from right, delay the fading in
                # of the description (looks nicer)
                sleep_time = 0
                starts = start.split(',')

                if ( int(starts[0]) >= 0 ) and ( self.DESCRIPTION_X == 0 ):
                    sleep_time = time_in
                elif ( int(starts[0]) < 0 ) and ( self.DESCRIPTION_X == 1 ):
                    sleep_time = time_in
                elif ( self.DESCRIPTION_X == 2 ):
                    sleep_time = int( time_in / 2 )

                if ( int(starts[1]) >= 0 ) and ( self.DESCRIPTION_Y == 'top' ):
                    sleep_time = time_in
                elif ( int(starts[1]) < 0 ) and ( self.DESCRIPTION_Y == 'bottom' ):
                    sleep_time = time_in
                #xbmc.sleep(sleep_time)
                time.sleep(float(sleep_time) / 1000)

            for label_control in black_label_controls + white_label_controls:
                label_control.setVisible(True)

        if self.recycle is False:
            #xbmc.sleep(time_in + 10)
            time.sleep( float(time_in + 10) / 1000 )
        else:
            #xbmc.sleep(time_in)
            time.sleep(float(time_in) / 1000 )

        top_image_control.setVisible(True)

        try:
            del self.image_dates[image_url]
        except KeyError:
           pass

        if ( initiating is True ):

            # Reset the timing
            self.recycle = False
            self.EFFECT_SPEED = save_EFFECT_SPEED
            self.NEXT_IMAGE_TIME = save_NEXT_IMAGE_TIME


def cycle(iterable):
    saved = []
    for element in iterable:
        yield element
        saved.append(element)
    while saved:
        for element in saved:
            yield element


if __name__ == '__main__':
    screensaver = ScreensaverManager()
    screensaver.start_loop()
    screensaver.close()
    del screensaver
    sys.modules.clear()
