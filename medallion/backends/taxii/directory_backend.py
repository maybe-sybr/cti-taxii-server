import codecs
import copy
import datetime
import json
import logging
import os
import re

from ...exceptions import ProcessingError
from ...filters.basic_filter import BasicFilter
from ...utils.common import (create_bundle, determine_spec_version,
                             determine_version, format_datetime,
                             generate_status, iterpath)
from .base import Backend

# Module-level logger
log = logging.getLogger(__name__)


class DirectoryBackend(Backend):
    # access control is handled at the views level

    def __init__(self, **kwargs):
        self.root_path = kwargs.get("path")
        self.save_status = kwargs.get("save_status", True)
        self.cache = {}
        self._init_discovery()
        self._init_api_roots()

    @staticmethod
    def _timestamp2filename(timestamp):
        """Takes timestamp string and removes some characters for normalized name"""
        ts = re.sub(r"[-T:\.Z ]", "", timestamp)
        return ts

    @staticmethod
    def _get_modified_timestamp(fp):
        fp_modified = os.path.getmtime(fp)
        dt = datetime.datetime.utcfromtimestamp(fp_modified)
        modified = format_datetime(dt)

        return modified

    @staticmethod
    def _save_file(obj, filename, *paths):
        final_path = os.path.join(*paths)
        if os.path.isdir(final_path) is False:
            os.makedirs(final_path)
        with codecs.open(os.path.join(final_path, filename), mode="w", encoding="utf8") as infile:
            json.dump(obj, infile, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _load_file(*paths):
        final_path = os.path.join(*paths)
        with codecs.open(final_path, mode="r", encoding="utf8") as infile:
            return json.load(infile)

    def _update_manifest(self, new_obj, api_root, collection_id, request_time):
        api_info = self._get(api_root)
        collections = api_info.get("collections", [])
        media_type_fmt = "application/vnd.oasis.stix+json; version={}"
        media_type = media_type_fmt.format(determine_spec_version(new_obj))
        version = determine_version(new_obj, request_time)

        obj_type, obj_id = new_obj["id"].split("--")
        json_name = self._timestamp2filename(version) + ".json"

        for collection in collections:
            if collection_id == collection["id"]:
                if "manifest" not in collection:
                    collection["manifest"] = []
                for entry in collection["manifest"]:
                    if new_obj["id"] == entry["id"]:
                        if "modified" in new_obj:
                            entry["versions"].append(new_obj["modified"])
                            entry["versions"] = sorted(entry["versions"], reverse=True)
                        # If the new_obj is there, and it has no modified
                        # property, then it is immutable, and there is nothing
                        # to do.
                        break
                else:
                    entry = {
                        "id": new_obj["id"],
                        "date_added": format_datetime(request_time),
                        "versions": [version],
                        "media_types": [media_type],
                    }
                    collection["manifest"].append(entry)
                    self._save_file(entry, json_name, self.root_path, api_root, collection_id, "manifest", obj_type, obj_id)
                self._save_file(new_obj, json_name, self.root_path, api_root, collection_id, "objects", obj_type, obj_id)

                # if the media type is new, attach it to the collection
                if media_type not in collection["media_types"]:
                    collection["media_types"].append(media_type)
                    collection_cp = copy.deepcopy(collection)
                    collection_cp.pop("manifest", None)
                    collection_cp.pop("modified", None)
                    collection_cp.pop("objects", None)

                    json_name = collection["id"] + ".json"
                    self._save_file(collection_cp, json_name, self.root_path, api_root, collection_id)

                # quit once you have found the collection that needed updating
                break

    def _get(self, key):
        for ancestors, item in iterpath(self.cache):
            if key in ancestors:
                return item

    def _init_discovery(self):
        if not self.root_path:
            raise ProcessingError('path was not specified in the config file', 400)

        if os.path.isdir(self.root_path) is False:
            raise ProcessingError("directory '{}' was not found".format(self.root_path), 500)

        discovery_path = os.path.join(self.root_path, "discovery.json")
        self.cache["/discovery"] = self._load_file(discovery_path)

    def _init_api_roots(self):
        if not self.root_path:
            raise ProcessingError('path was not specified in the config file', 400)

        if os.path.isdir(self.root_path) is False:
            raise ProcessingError("directory '{}' was not found".format(self.root_path), 500)

        for dirpath, dirnames, filenames in os.walk(self.root_path):
            for dirname in dirnames:
                filename = dirname + ".json"
                file_path = os.path.join(dirpath, dirname, filename)
                if os.path.isfile(file_path):
                    self.cache[dirname] = {}
                    self.cache[dirname]["information"] = self._load_file(file_path)
                    self.cache[dirname]["collections"] = []
                    self.cache[dirname]["status"] = []
                    self.cache[dirname]["modified"] = self._get_modified_timestamp(file_path)
                    self._init_collections(dirname, os.path.join(dirpath, dirname))
            break

    def _init_collections(self, api_root, apiroot_path):
        if not self.root_path:
            raise ProcessingError('path was not specified in the config file', 400)

        if os.path.isdir(self.root_path) is False:
            raise ProcessingError("directory '{}' was not found".format(self.root_path), 500)

        for dirpath, dirnames, filenames in os.walk(apiroot_path):
            for dirname in dirnames:
                filename = dirname + ".json"
                file_path = os.path.join(dirpath, dirname, filename)
                if os.path.isfile(file_path):
                    collection = self._load_file(file_path)
                    collection["manifest"] = []
                    collection["objects"] = []
                    collection["modified"] = self._get_modified_timestamp(file_path)
                    self.cache[api_root]["collections"].append(collection)
                    self._init_resource(collection, os.path.join(dirpath, dirname), "objects")
                    self._init_resource(collection, os.path.join(dirpath, dirname), "manifest")

    def _init_resource(self, collection, collection_path, resource):
        if not self.root_path:
            raise ProcessingError('path was not specified in the config file', 400)

        if os.path.isdir(self.root_path) is False:
            raise ProcessingError("directory '{}' was not found".format(self.root_path), 500)

        collection_path = os.path.join(collection_path, resource)

        for dirpath, dirnames, filenames in os.walk(collection_path):
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                if os.path.isfile(file_path):
                    loaded_obj = self._load_file(file_path)
                    collection[resource].append(loaded_obj)

    def server_discovery(self):
        if "/discovery" in self.cache:
            return self._get("/discovery")

    def get_api_root_information(self, api_root):
        if api_root in self.cache:
            api_info = self._get(api_root)

            if "information" in api_info:
                return api_info["information"]

    def get_collections(self, api_root, start_index, end_index):
        if api_root not in self.cache:
            return None, None  # must return None so 404 is raised

        api_info = self._get(api_root)
        collections = copy.deepcopy(api_info.get("collections", []))
        count = len(collections)

        collections = collections[start_index:end_index]
        # Remove data that is not part of the response.
        for collection in collections:
            collection.pop("manifest", None)
            collection.pop("modified", None)
            collection.pop("objects", None)
        return count, collections

    def get_collection(self, api_root, collection_id):
        if api_root in self.cache:
            api_info = self._get(api_root)
            collections = copy.deepcopy(api_info.get("collections", []))

            for collection in collections:
                if collection_id == collection["id"]:
                    collection.pop("manifest", None)
                    collection.pop("modified", None)
                    collection.pop("objects", None)
                    return collection

    def get_objects(self, api_root, collection_id, filter_args, allowed_filters, start_index, end_index):
        if api_root in self.cache:
            api_info = self._get(api_root)
            collections = api_info.get("collections", [])
            objs = []

            for collection in collections:
                if collection_id == collection["id"]:
                    full_filter = BasicFilter(filter_args)
                    objs = full_filter.process_filter(
                        collection.get("objects", []),
                        allowed_filters,
                        collection.get("manifest", []),
                    )
                    break

            count = len(objs)
            result = objs[start_index:end_index]
            return count, create_bundle(result)

    def get_object(self, api_root, collection_id, object_id, filter_args, allowed_filters):
        if api_root in self.cache:
            api_info = self._get(api_root)
            collections = api_info.get("collections", [])

            objs = []
            manifests = []
            for collection in collections:
                if collection_id == collection["id"]:
                    for obj in collection.get("objects", []):
                        if object_id == obj["id"]:
                            objs.append(obj)
                    manifests = collection.get("manifest", [])
                    break

            full_filter = BasicFilter(filter_args)
            objs = full_filter.process_filter(
                objs,
                allowed_filters,
                manifests
            )
            return create_bundle(objs)

    def get_object_manifest(self, api_root, collection_id, filter_args, allowed_filters, start_index, end_index):
        if api_root in self.cache:
            api_info = self._get(api_root)
            collections = api_info.get("collections", [])

            for collection in collections:
                if collection_id == collection["id"]:
                    full_filter = BasicFilter(filter_args)
                    manifest = full_filter.process_filter(
                        collection.get("manifest", []),
                        allowed_filters,
                        None,
                    )

                    count = len(manifest)
                    result = manifest[start_index:end_index]
                    return count, result

    def add_objects(self, api_root, collection_id, objs, request_time):
        if api_root in self.cache:
            api_info = self._get(api_root)
            collections = api_info.get("collections", [])
            failed = 0
            succeeded = 0
            pending = 0
            successes = []
            failures = []

            for collection in collections:
                if collection_id == collection["id"]:
                    if "objects" not in collection:
                        collection["objects"] = []
                    try:
                        for new_obj in objs["objects"]:
                            id_and_version_already_present = False
                            for obj in collection["objects"]:
                                if new_obj["id"] == obj["id"]:
                                    if "modified" in new_obj and new_obj["modified"] == obj["modified"]:
                                        id_and_version_already_present = True
                                    else:
                                        # There is no modified field, so this object is immutable
                                        id_and_version_already_present = True
                                    break
                            if id_and_version_already_present is False:
                                collection["objects"].append(new_obj)
                                self._update_manifest(new_obj, api_root, collection["id"], request_time)
                                successes.append(new_obj["id"])
                                succeeded += 1
                            else:
                                failures.append({
                                    "id": new_obj["id"],
                                    "message": "Unable to process object because identical version exist."
                                })
                                failed += 1
                    except Exception as e:
                        log.exception(e)
                        raise ProcessingError("While processing supplied content, an error occurred", 422, e)

            status = generate_status(
                format_datetime(request_time), "complete", succeeded,
                failed, pending, successes_ids=successes,
                failures=failures,
            )
            api_info["status"].append(status)
            if self.save_status:
                self._save_file(status, status["id"] + ".json", self.root_path, api_root, "status")
            return status

    def get_status(self, api_root, status_id):
        if api_root in self.cache:
            api_info = self._get(api_root)

            for status in api_info.get("status", []):
                if status_id == status["id"]:
                    return status
