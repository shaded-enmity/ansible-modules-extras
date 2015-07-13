#!/usr/bin/python -tt
# -*- coding: utf-8 -*-
#
# Copyright 2015 Pavel Odvody <podvody@redhat.com>
# Copyright 2015 Cristian van Ee <cristian at cvee.org>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.
#

import os
NO_DNF = False
try:
        import dnf
except ImportError:
        NO_DNF = True

DOCUMENTATION = '''
---
module: dnf
version_added: 1.9
short_description: Manages packages with the I(dnf) package manager
description:
     - Installs, upgrade, removes, and lists packages and groups with the I(dnf) package manager.
options:
  name:
    description:
      - "Package name, or package specifier with version, like C(name-1.0). When using state=latest, this can be '*' which means run: dnf -y update. You can also pass a url or a local path to a rpm file."
    required: true
    default: null
    aliases: []
  list:
    description:
      - Various (non-idempotent) commands for usage with C(/usr/bin/ansible) and I(not) playbooks. See examples.
    required: false
    default: null
  state:
    description:
      - Whether to install (C(present), C(latest)), or remove (C(absent)) a package.
    required: false
    choices: [ "present", "latest", "absent" ]
    default: "present"
  enablerepo:
    description:
      - I(Repoid) of repositories to enable for the install/update operation.
        These repos will not persist beyond the transaction.
        When specifying multiple repos, separate them with a ",".
    required: false
    default: null
    aliases: []

  disablerepo:
    description:
      - I(Repoid) of repositories to disable for the install/update operation.
        These repos will not persist beyond the transaction.
        When specifying multiple repos, separate them with a ",".
    required: false
    default: null
    aliases: []

  conf_file:
    description:
      - The remote dnf configuration file to use for the transaction.
    required: false
    default: null
    aliases: []

  disable_gpg_check:
    description:
      - Whether to disable the GPG checking of signatures of packages being
        installed. Has an effect only if state is I(present) or I(latest).
    required: false
    default: "no"
    choices: ["yes", "no"]
    aliases: []

notes: []
# informational: requirements for nodes
requirements:
  - dnf
  - "python >= 2.6"
author: 
  - '"Pavel Odvody" <podvody@redhat.com>'
  - '"Cristian van Ee (@DJMuggs)" <cristian at cvee.org>'
'''

EXAMPLES = '''
- name: install the latest version of Apache
  dnf: name=httpd state=latest

- name: remove the Apache package
  dnf: name=httpd state=absent

- name: install the latest version of Apache from the testing repo
  dnf: name=httpd enablerepo=testing state=present

- name: upgrade all packages
  dnf: name=* state=latest

- name: install the nginx rpm from a remote repo
  dnf: name=http://nginx.org/packages/centos/6/noarch/RPMS/nginx-release-centos-6-0.el6.ngx.noarch.rpm state=present

- name: install nginx rpm from a local file
  dnf: name=/usr/local/src/nginx-release-centos-6-0.el6.ngx.noarch.rpm state=present

- name: install the 'Development tools' package group
  dnf: name="@Development tools" state=present

'''

def init_dnf(module, repos=([], []), conf='', gpg=True):
        '''Initialize the `dnf.Base` object with the supplied configuration

        :param module: 
        :type module: AnsibleModule
        :param repos: Tuple of `Enabled`/`Disabled` repositories
        :type repos: (list, list)
        :param conf: Path to configuration file
        :type conf: str
        :param gpg: Pass `False` to disable `GPG` checking
        :return: Initialized :class:`dnf.Base` object
        '''
        obj = dnf.Base()
        if conf:
                if not os.path.exists(conf):
                        err = {'msg': 'Invalid configuration path',
                               'path': conf }
                        module.fail_json(**err)
                obj.conf.read(conf)

        if not gpg:
                obj.conf.gpgcheck = False

        obj.conf.assumeyes = True

        obj.read_all_repos()
        if repos[1]:
                for r in repos[1]:
                        obj.repos.get_matching(r).disable()

        if repos[0]:
                for r in repos[0]:
                        obj.repos.get_matching(r).enable()

        obj.fill_sack()
        obj.read_comps()

        return obj

def ansible_result(module, rc, changed, results):
        '''Helper function that exits `ansible` module

        :param module: Module to exit
        :type module: AnsibleModule
        :param rc: Return code
        :type rc: int
        :param changed: Did a change occur?
        :type changed: bool
        :param results: Result dictionary
        :type results: dict
        :return: Nothing
        '''
        o = {
                'rc' :      rc,
                'changed':  changed,
                'results':  results
        }
        module.exit_json(**o)

def get_group(dnfo, name):
        '''Finds package group with the given `name`

        :param dnfo: DNF object
        :type dnfo: dnf.Base
        :param name: Name
        :type name: str
        :return: Group with the supplied name
        :rtype: dnfo.comps.Group
        '''
        grp = dnfo.comps.group_by_pattern(name[1:])
        if not grp:
                raise dnf.exceptions.Error('Group {0} not found'.format(name))

        return grp

def query(c, name):
        '''Query information about package with the name `name` in
        the collection `c`.

        :param c: Package collection
        :type c: dnf.hawkey.Query
        :param name: Name 
        :type name: str
        :return: Package as a dictionary
        :rtype: dict
        '''
        i = c.filter(provides=name)
        if not i:
                raise dnf.exceptions.Error('Package {0} not found'.format(name))

        return format_pkg(i[-1])

def install(dnfo, names):
        '''Installs a package group or a list of packages

        :param dnfo: DNF object
        :type dnfo: dnf.Base 
        :param names: Packages to install
        :type names: list[str]
        :return: Group name or list of packages
        :rtype: str or list[dict]
        '''
        name = names[0]
        if name.startswith('@'):
                try:
                        dnfo.group_install(get_group(name), 'default')
                except dnf.exceptions.CompsError:
                        pass
                return { 'Installed': name}

        q = dnfo.sack.query()
        i = q.available()

        installed = []
        for n in names:
                dnfo.install(n)
                installed.append(query(i, n))

        return { 'Installed': installed }

def remove(dnfo, names):
        '''Removes a package group or a list of packages

        :param dnfo: DNF object
        :type dnfo: dnf.Base 
        :param names: Packages to remove
        :type names: list[str]
        :return: Group name or list of packages
        :rtype: str or list[dict]
        '''
        name = names[0]
        if name.startswith('@'):
                try:
                        dnfo.group_remove(get_group(name))
                except dnf.exceptions.CompsError:
                        pass
                return { 'Removed': name }

        q = dnfo.sack.query()
        i = q.installed()

        removed = []
        for n in names:
                try:
                        dnfo.remove(n)
                        removed.append(query(i, n))
                except dnf.exceptions.PackagesNotInstalledError:
                        pass

        return { 'Removed': removed }

def upgrade(dnfo, names):
        '''Upgrades a package group or a list of packages, or all `*`

        :param dnfo: DNF object
        :type dnfo: dnf.Base 
        :param names: Packages to upgrade, or `*`
        :type names: list[str]
        :return: Group name or list of packages
        :rtype: str or list[dict]
        '''
        name = names[0]
        if name == '*':
                dnfo.upgrade_all()
                return { 'Upgraded': 'all' }

        if name.startswith('@'):
                try:
                        dnfo.group_upgrade(get_group(name))
                except dnf.exceptions.CompsError:
                        pass
                return { 'Upgraded': name }

        q = dnfo.sack.query()
        a = q.available()
        i = q.installed()

        installed, upgraded = [], []
        for n in names:
                i = i.filter(provides=n)
                a = a.filter(provides=n)
                if not i and a:
                        installed.append(format_pkg(a[-1]))
                        dnfo.install(n)
                elif i and a: 
                        upgraded.append(format_pkg(a[-1]))
                        dnfo.upgrade(n)
                elif not i and not a:
                        raise dnf.exceptions.Error('Package {0} not found'.format(n))

        return {
                'Installed': installed,
                'Upgraded': upgraded
        }

def handle_state(dnfo, state, name):
        '''Packages specified in `name` should converge to particular `state`

        :param dnfo: DNF object
        :type dnfo: dnf.Base 
        :param state: State to converge to
        :type state: str
        :param name: Package names
        :type name: list[str]
        :return: Dictionary of installed/removed/upgraded packages 
        :rtype: dict or `None` if state was invalid
        '''
        " :type dnfo: dnf.Base "
        if state in ['present', 'installed']:
                return install(dnfo, name)
        elif state in ['absent', 'removed']:
                return remove(dnfo, name)
        elif state == 'latest':
                return upgrade(dnfo, name)

        return None

def format_pkg(p):
        '''Format a package `p` as a dictionary

        :param p: Package to format
        :type p: dnf.hawkey.Package
        :return: Formatted package
        :rtype: dict
        '''
        return {
                'name': p.name,
                'arch': p.arch,
                'release': p.release,
                'version': p.version,
                'epoch': p.epoch,
                'repo': p.reponame,
                'nevra': "{0}:{1}-{2}-{3}.{4}".format(p.name, p.epoch, p.version, p.release, p.arch)

        }

def pkg_list(lst):
        '''Outputs a sorted list of package dictionaries

        :param lst: List of packages
        :type lst: list[dnf.hawkey.Package]
        :return: Sorted list of formatted packages
        :rtype: list[dict]
        '''
        return sorted([format_pkg(p) for p in lst])

def get_pkg_list(query, collection):
        '''Queries the `query` object about the packages
        in specified `collection`. Formats the output as a list of
        package dictionaries

        :param query: Query object
        :type query: dnf.hawkey.Query
        :param collection: Collection name
        :type collection: str
        :return: List of packages in the specified `collection`
        :rtype: list[dict]
        '''
        return pkg_list(getattr(query, collection)())

def handle_list(dnfo, what):
        '''Provides entrypoint for `repo`/`package` listing functionality

        :param dnfo: DNF object
        :type dnfo: dnf.Base 
        :param what: What collection to list
        :type what: str
        :return: List of package dictionaries
        :rtype: list[dict]
        '''
        " :type dnfo: dnf.Base "
        if what == 'repos':
                return [k for (k, _) in dnfo.repos.iteritems()]
        else:
                q = dnfo.sack.query()
                if what == 'installed':
                        return get_pkg_list(q, 'installed')
                elif what == 'available':
                        return get_pkg_list(q, 'available')
                elif what == 'updates':
                        return get_pkg_list(q, 'upgrades')

                return []

def main():
        module = AnsibleModule(
                argument_spec = dict(
                        name=dict(aliases=['pkg'], type='list'),
                        state=dict(default='installed', choices=['absent','present','installed','removed','latest']),
                        enablerepo=dict(type='list'),
                        disablerepo=dict(type='list'),
                        list=dict(),
                        conf_file=dict(default=None),
                        disable_gpg_check=dict(required=False, default="no", type='bool'),
                ),
                required_one_of = [['name','list']],
                mutually_exclusive = [['name','list']],
                supports_check_mode = True
        )

        if NO_DNF:
                err = {'msg': 'DNF was not found on this system'}
                module.fail_json(**err)

        params = module.params

        enabled = params.get('enablerepo')
        disabled = params.get('disablerepo')

        with init_dnf(
                module = module,
                repos  = (enabled, disabled), 
                conf   = params['conf_file'], 
                gpg    = not params['disable_gpg_check']
        ) as dnfo:
                if params['list']:
                        ansible_result(module, 0, True, handle_list(dnfo, params['list']))
                else:
                        pkg = params['name']
                        state = params['state']

                        if not pkg:
                                err = {'err': 'No package specified'}
                                module.fail_json(**err)

                        pkg_name = pkg[0]
                        names = handle_state(dnfo, state, pkg)

                        dnfo.resolve(True)

                        if state == 'latest' and (pkg == '*' or pkg_name.startswith('@')):
                                names['Upgraded'] = pkg_list(dnfo.transaction.install_set)
                        elif state in ['present', 'installed'] and pkg_name.startswith('@'):
                                names['Installed'] = pkg_list(dnfo.transaction.install_set)
                        elif state in ['absent', 'removed'] and pkg_name.startswith('@'):
                                names['Removed'] = pkg_list(dnfo.transaction.remove_set)

                        dnfo.download_packages(dnfo.transaction.install_set)
                        dnfo.do_transaction()

                        ansible_result(module, 0, True, names)

from ansible.module_utils.basic import *
if __name__ == '__main__':
        main()
