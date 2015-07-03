#!/usr/bin/python -tt
# vim: set fileencoding=utf-8
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
import dnf

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
        obj = dnf.Base()
        if conf:
                if not os.path.exists(conf):
                        err = { msg: 'Invalid configuration path',
                                path: conf }
                        module.fail_json(**err)
                obj.conf.read(conf)

        if not gpg:
                obj.conf.gpgcheck = False

        obj.conf.assumeyes = True

        if repos[1]:
                for r in repos[1]:
                        obj.repos.get_matching(r).disable()

        if repos[0]:
                for r in repos[0]:
                        obj.repos.get_matching(r).enable()

        obj.read_all_repos()
        obj.fill_sack()
        obj.read_comps()

        return obj

def ansible_result(module, rc, changed, results):
        o = {
                'rc' :      rc,
                'changed':  changed,
                'results':  results
        }
        module.exit_json(**o)

def get_names(name):
        if ',' not in name:
                return [name]
        return name.split(',')

def get_group(dnfo, name):
        grp = dnfo.comps.group_by_pattern(name[1:])
        if not grp:
                raise dnf.exceptions.Error('Group {0} not found'.format(name))

        return grp

def query(c, name):
        i = c.filter(provides=name)
        if not i:
                raise dnf.exceptions.Error('Package {0} not found'.format(name))

        return format_pkg(i[-1])

def install(dnfo, name):
        " :type dnfo: dnf.Base "
        if name.startswith('@'):
                try:
                        dnfo.group_install(get_group(name), 'default')
                except dnf.exceptions.CompsError:
                        pass
                return { 'Installed': name}

        q = dnfo.sack.query()
        i = q.available()

        installed = []
        names = get_names(name)
        for n in names:
                dnfo.install(n)
                installed.append(query(i, n))

        return { 'Installed': installed }

def remove(dnfo, name):
        " :type dnfo: dnf.Base "
        if name.startswith('@'):
                try:
                        dnfo.group_remove(get_group(name))
                except dnf.exceptions.CompsError:
                        pass
                return { 'Removed': name }

        q = dnfo.sack.query()
        i = q.installed()

        removed = []
        names = get_names(name)
        for n in names:
                try:
                        dnfo.remove(n)
                        removed.append(query(i, n))
                except dnf.exceptions.PackagesNotInstalledError:
                        pass

        return { 'Removed': removed }

def upgrade(dnfo, name):
        " :type dnfo: dnf.Base "
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
        names = get_names(name)
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
        " :type dnfo: dnf.Base "
        if state in ['present', 'installed']:
                return install(dnfo, name)
        elif state in ['absent', 'removed']:
                return remove(dnfo, name)
        elif state == 'latest':
                return upgrade(dnfo, name)

        return None

def format_pkg(p):
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
        return sorted([format_pkg(p) for p in lst])

def get_pkg_list(query, collection):
        return pkg_list(getattr(query, collection)())

def handle_list(dnfo, what):
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

def parse_repolist(repos):
        if not repos:
                return []

        if repos == '*' or ',' not in repos:
                return [repos]

        return repos.split(',')

def main():
        module = AnsibleModule(
                argument_spec = dict(
                        name=dict(aliases=['pkg']),
                        state=dict(default='installed', choices=['absent','present','installed','removed','latest']),
                        enablerepo=dict(),
                        disablerepo=dict(),
                        list=dict(),
                        conf_file=dict(default=None),
                        disable_gpg_check=dict(required=False, default="no", type='bool'),
                ),
                required_one_of = [['name','list']],
                mutually_exclusive = [['name','list']],
                supports_check_mode = True
        )

        params = module.params

        enablerepo = params.get('enablerepo', '')
        disablerepo = params.get('disablerepo', '')

        enabled = parse_repolist(enablerepo)
        disabled = parse_repolist(disablerepo)

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

                        names = handle_state(dnfo, state, pkg)

                        dnfo.resolve(True)

                        if state == 'latest' and (pkg == '*' or pkg.startswith('@')):
                                names['Upgraded'] = pkg_list(dnfo.transaction.install_set)
                        elif state in ['present', 'installed'] and pkg.startswith('@'):
                                names['Installed'] = pkg_list(dnfo.transaction.install_set)
                        elif state in ['absent', 'removed'] and pkg.startswith('@'):
                                names['Removed'] = pkg_list(dnfo.transaction.remove_set)

                        dnfo.download_packages(dnfo.transaction.install_set)
                        dnfo.do_transaction()

                        ansible_result(module, 0, True, names)

from ansible.module_utils.basic import *
if __name__ == '__main__':
        main()
