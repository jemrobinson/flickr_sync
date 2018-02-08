#! /usr/bin/env python
import argparse
import flickrapi
import os
import time
import PIL.Image
import PIL.ExifTags
import ConfigParser

POST_INTERVAL = 0.1


class Photo(object):
    """! Basic class to hold information about a photo."""

    def __init__(self, unique_id, taken=None, modified=None):
        """! Constructor."""
        self.unique_id = unique_id
        self.taken = taken
        if modified is None:
            self.modified = os.path.getmtime(self.unique_id)
        else:
            self.modified = modified


def getLocalPhotos(PHOTO_FOLDER):
    """! Check local search path for photos."""
    images = {}
    for (dirpath, __dirnames, file_names) in os.walk(PHOTO_FOLDER):
        if ".picasaoriginals" not in dirpath:
            for file_name in file_names:
                name, ext = os.path.splitext(file_name)
                if ext == ".jpg" or ext == ".gif" or ext == ".png":
                    images[name] = Photo(unique_id=os.path.normpath(os.path.join(dirpath, file_name)))
    return images


def getRemotePhotos():
    """! Get metadata for remote photos."""
    images = {}
    nPhotos = int(flickr.people.getPhotos(user_id="me")["photos"]["total"])
    NPERPAGE = 500
    print "Checking {} remote photos".format(nPhotos)
    # Get photos page-by-page
    nPages = (nPhotos / NPERPAGE) + 1
    for page in range(1, nPages + 1):
        print "... requesting page {} / {}".format(page, nPages)
        remote_photos = flickr.people.getPhotos(user_id="me", page=page, per_page=NPERPAGE)
        for photo in remote_photos["photos"]["photo"]:
            photo_info = flickr.photos.getInfo(photo_id=photo["id"])
            name = photo["title"]
            if name in images.keys() and photo_info["photo"]["dates"]["taken"] == images[name].taken:
                print "  ... found duplicate uploads named '{}' (removing instance with id={})".format(name, photo["id"])
                flickr.photos.delete(photo_id=photo["id"])
                continue
            images[name] = Photo(unique_id=photo["id"], taken=photo_info["photo"]["dates"]["taken"], modified=photo_info["photo"]["dates"]["lastupdate"])
        print "... loaded metadata for {} unique photos".format(len(images))
    return images


def uploadPhotos(names, local_photos, remote_photos, replace=False):
    """! Upload photos."""
    for name in names:
        upload_params = {"filename": local_photos[name].unique_id, "format": "rest"}
        success = True
        if replace:
            upload_params["photo_id"] = remote_photos[name].unique_id
            try:
                flickr.replace(**upload_params)
            except flickrapi.exceptions.FlickrError:
                success = False
        else:
            upload_params["is_family"] = "0"
            upload_params["is_friend"] = "0"
            upload_params["is_public"] = "0"
            upload_params["tags"] = "auto-upload"
            upload_params["title"] = name
            try:
                flickr.upload(**upload_params)
            except flickrapi.exceptions.FlickrError:
                success = False
        if success:
            print "... uploaded '{}'".format(name)
        else:
            print "... upload failed for '{}'".format(name)
        time.sleep(POST_INTERVAL)


def check_EXIF(photos):
    """! Ensure that dates are included in the EXIF tags."""
    problematic_paths = []
    for name, photo in photos.items():
        has_date = False
        try:
            for (k, v) in PIL.Image.open(photo.unique_id)._getexif().iteritems():
                if PIL.ExifTags.TAGS.get(k) == "DateTimeOriginal":
                    has_date = True
        except AttributeError:
            pass
        if not has_date:
            problematic_paths.append(photo.unique_id)
    if not problematic_paths:
        print "No files found with problematic EXIF data"
    else:
        print "Found {} files with problematic EXIF data:".format(len(problematic_paths))
        for _p in sorted(problematic_paths):
            print _p


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Synchronise local folder with Flickr.")
    parser.add_argument("-f", "--folder", action="store", help="Local folder to use (will default to last used otherwise)")
    parser.add_argument("-e", "--skipEXIF", action="store_true", help="Skip EXIF check on local files")
    parser.add_argument("-u", "--skipUpload", action="store_true", help="Skip upload of new files")
    parser.add_argument("-r", "--skipReplace", action="store_true", help="Skip replacement of files which have been updated since upload")
    args = parser.parse_args()

    # Create config file if it does not exist
    config = ConfigParser.RawConfigParser()
    if not os.path.isfile("flickr.cfg"):
        print "Creating local config file..."
        config.add_section("flickr")
        config.set("flickr", "key", raw_input("Enter your flickr API key: "))
        config.set("flickr", "secret", raw_input("Enter your flickr API secret: "))
        config.set("flickr", "photo_folder", raw_input("Enter full path to photo folder: "))
        with open("flickr.cfg", "wb") as f_config:
            config.write(f_config)
    # Update folder
    elif args.folder:
        config.read("flickr.cfg")
        config.set("flickr", "photo_folder", args.folder)
        with open("flickr.cfg", "wb") as f_config:
            config.write(f_config)

    # Read flickr information from config file
    config.read("flickr.cfg")
    API_KEY = config.get("flickr", "key")
    API_SECRET = config.get("flickr", "secret")
    PHOTO_FOLDER = config.get("flickr", "photo_folder")

    flickr = flickrapi.FlickrAPI(API_KEY, API_SECRET, format="parsed-json", token_cache_location=".")
    flickr.authenticate_via_browser(perms="delete")

    local_photos = getLocalPhotos(PHOTO_FOLDER)
    print "Found {} existing local photos".format(len(local_photos))
    if not args.skipEXIF:
        check_EXIF(local_photos)

    remote_photos = getRemotePhotos()
    print "Found {} existing remote photos".format(len(remote_photos))

    local_names = set(local_photos.keys())
    remote_names = set(remote_photos.keys())
    overlap_names = local_names.intersection(remote_names)

    names_to_delete = remote_names.difference(local_names)
    names_to_upload = local_names.difference(remote_names)
    names_to_replace = set([])

    # Check for remote photos which have been deleted
    print "Removing {} previously uploaded photo(s) which do not exist locally".format(len(names_to_delete))
    for name in names_to_delete:
        flickr.photos.delete(photo_id=remote_photos[name].unique_id)

    # Check for local modifications
    print "Checking for local modifications to {} previously uploaded photos...".format(len(overlap_names))
    for overlap_name in overlap_names:
        if local_photos[overlap_name].modified > remote_photos[overlap_name].modified:
            print "For {}, the local version is newer".format(overlap_name)
            names_to_replace.add(overlap_name)
    print "There are {} files to upload, including {} replacements".format(len(names_to_upload) + len(names_to_replace), len(names_to_replace))

    # Upload new photos
    if not args.skipUpload:
        uploadPhotos(names_to_upload, local_photos, remote_photos)

    # Replace modified photos
    if not args.skipReplace:
        uploadPhotos(names_to_replace, local_photos, remote_photos, replace=True)
