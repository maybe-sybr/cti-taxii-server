import json
import pathlib

import jsonmerge

DEFAULT_CONFFILE = "/etc/medallion.conf"
DEFAULT_CONFDIR = "/etc/medallion.d/"


def load_config(conf_file=DEFAULT_CONFFILE, conf_dir=DEFAULT_CONFDIR):
    if conf_file is not None:
        conf_file_p = pathlib.Path(conf_file)
        try:
            config_data = json.load(conf_file_p.open())
        except FileNotFoundError as exc:
            if conf_file is not DEFAULT_CONFFILE:
                raise exc
            else:
                config_data = dict()
    if conf_dir is not None:
        conf_dir_p = pathlib.Path(conf_dir)
        try:
            for conf_file_p in conf_dir_p.iterdir():
                if conf_file_p.suffix in {".json", ".conf"}:
                    try:
                        new_data = json.load(conf_file_p.open())
                    except IsADirectoryError:
                        pass
                    else:
                        config_data = jsonmerge.merge(config_data, new_data)
        except FileNotFoundError as exc:
            if conf_dir is not DEFAULT_CONFDIR:
                raise exc
    return config_data
