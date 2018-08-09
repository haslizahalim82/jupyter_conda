# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.

import json
import os
import re
import typing

from pkg_resources import parse_version
from subprocess import Popen, PIPE

from traitlets.config.configurable import LoggingConfigurable
from traitlets import Dict


def pkg_info(s):
    try:
        # for conda >= 4.3, `s` should be a dict
        name = s['name']
        version = s['version']
        build = s.get('build_string') or s['build']
    except TypeError:
        # parse legacy string version for information
        name, version, build = s.rsplit('-', 2)

    return {
        'name': name,
        'version': version,
        'build': build
    }


MAX_LOG_OUTPUT = 6000

CONDA_EXE = os.environ.get('CONDA_EXE', 'conda')

# try to match lines of json
JSONISH_RE = r'(^\s*["\{\}\[\],\d])|(["\}\}\[\],\d]\s*$)'

# these are the types of environments that can be created
package_map = {
    'python2': 'python=2 ipykernel',
    'python3': 'python=3 ipykernel',
    'r':       'r-base r-essentials',
}


class EnvManager(LoggingConfigurable):
    envs = Dict()

    def _execute(self, cmd: str, *args) -> str:
        cmdline = cmd.split() + list(args)
        self.log.debug('[nb_conda] command: %s', cmdline)

        process = Popen(cmdline, stdout=PIPE, stderr=PIPE)
        output, error = process.communicate()

        if process.returncode == 0:
            output = output.decode('utf-8').splitlines()
        else:
            self.log.debug('[nb_conda] exit code: %s', process.returncode)
            output = error.decode("utf-8")

        self.log.debug('[nb_conda] output: %s', output[:MAX_LOG_OUTPUT])

        if len(output) > MAX_LOG_OUTPUT:
            self.log.debug('[nb_conda] ...')

        return output

    def list_envs(self) -> typing.Dict[str, str]:
        """List all environments that conda knows about"""
        info = self.clean_conda_json(self._execute(CONDA_EXE + ' info',
                                                   '--json'))
        default_env = info['default_prefix']

        root_env = {
                'name': 'base',
                'dir': info['root_prefix'],
                'is_default': info['root_prefix'] == default_env
        }

        def get_info(env):
            return {
                'name': os.path.basename(env),
                'dir': env,
                'is_default': env == default_env
            }

        return {
            "environments": [root_env] +
            [get_info(env)
             for env in info['envs'] if env != info['root_prefix'] and env.startswith(info['root_prefix'])]
        }

    def delete_env(self, env: str) -> dict:
        output = self._execute(CONDA_EXE + ' env remove -y -q --json -n', env)
        return self.clean_conda_json(output)

    def clean_conda_json(self, output: str) -> dict:
        lines = output.splitlines()

        try:
            return json.loads('\n'.join(lines))
        except (ValueError, json.JSONDecodeError) as err:
            self.log.warn('[nb_conda] JSON parse fail:\n%s', err)

        # try to remove bad lines
        lines = [line for line in lines if re.match(JSONISH_RE, line)]

        try:
            return json.loads('\n'.join(lines))
        except (ValueError, json.JSONDecodeError) as err:
            self.log.error('[nb_conda] JSON clean/parse fail:\n%s', err)

        return {"error": True}

    def export_env(self, env: str) -> str:
        return self._execute(CONDA_EXE + ' list -e -n', env)

    def clone_env(self, env: str, name: str) -> dict:
        output = self._execute(CONDA_EXE + ' create -y -q --json -n', name,
                               '--clone', env)
        return self.clean_conda_json(output)

    def create_env(self, env: str, type: str) -> dict:
        packages = package_map[type]
        output = self._execute(CONDA_EXE + ' create -y -q --json -n', env,
                               *packages.split(" "))
        return self.clean_conda_json(output)

    def env_packages(self, env: str) -> dict:
        output = self._execute(CONDA_EXE + ' list --no-pip --json -n', env)
        data = self.clean_conda_json(output)
        if 'error' in data:
            # we didn't get back a list of packages, we got a dictionary with
            # error info
            return data

        return {
            "packages": [pkg_info(package) for package in data]
        }

    def check_update(self, env: str, packages: typing.List[str]) -> dict:
        output = self._execute(CONDA_EXE + ' update --dry-run -q --json -n',
                               env, *packages)
        data = self.clean_conda_json(output)

        if 'error' in data:
            # we didn't get back a list of packages, we got a dictionary with
            # error info
            return data
        elif 'actions' in data:
            links = data['actions'].get('LINK', [])
            package_versions = [link.get('dist_name') for link in links]
            return {
                "updates": [pkg_info(pkg_version)
                            for pkg_version in package_versions]
            }
        else:
            # no action plan returned means everything is already up to date
            return {
                "updates": []
            }

    def install_packages(self, env: str, packages: typing.List[str]) -> dict:
        output = self._execute(CONDA_EXE + ' install -y -q --json -n',
                               env, *packages)
        return self.clean_conda_json(output)

    def update_packages(self, env: str, packages: typing.List[str]) -> dict:
        output = self._execute(CONDA_EXE + ' update -y -q --json -n',
                               env, *packages)
        return self.clean_conda_json(output)

    def remove_packages(self, env: str, packages: typing.List[str]) -> dict:
        output = self._execute(CONDA_EXE + ' remove -y -q --json -n',
                               env, *packages)
        return self.clean_conda_json(output)

    def package_search(self, q: str) -> dict:
        # this method is slow and operates synchronously
        output = self._execute(CONDA_EXE + ' search --json', q)
        data = self.clean_conda_json(output)

        if 'error' in data:
            # we didn't get back a list of packages, we got a dictionary with
            # error info
            return data

        packages = []

        for name, entries in data.items():
            max_version = None
            max_version_entry = None

            for entry in entries:
                version = parse_version(entry.get('version', ''))

                if max_version is None or version > max_version:
                    max_version = version
                    max_version_entry = entry

            packages.append(max_version_entry)
        return {
            "packages": sorted(packages, key=lambda entry: entry.get('name'))
        }

