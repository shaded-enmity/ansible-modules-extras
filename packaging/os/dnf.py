#!/usr/bin/python -tt
# vim: set fileencoding=utf-8
# Written by Pavel Odvody <podvody@redhat.com>
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


import traceback
import os
import dnf
import syslog

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
author: "Pavel Odvody"
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

class AnsibleDnfException(Exception):
        pass

def init_dnf(repos = ([], []), conf = '', gpg = True):
        obj = dnf.Base()
        if conf:
                if not os.path.exists(conf):
                        raise AnsibleDnfException(
                                'Invalid configuration path: {0}'.format(conf)
                        )
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

        return obj

def ansible_result(module, rc, msg, changed, results):
    o = {
        'rc' :      rc,
        'msg':      msg,
        'changed':  changed,
        'results':  results
    }
    module.exit_json(**o)

def get_names(name):
        if ',' not in name:
                return [name]
        return name.split(',')

def install(dnfo, name):
        " :type dnfo: dnf.Base "
        if name.startswith('@'):
                dnfo.group_install(name, 'default')
                return [name]

        names = get_names(name)
        for n in names:
                dnfo.install(n)

        return names

def remove(dnfo, name):
        " :type dnfo: dnf.Base "
        if name.startswith('@'):
                dnfo.group_remove(name)
                return [name]

        names = get_names(name)
        for n in names:
                try:
                        dnfo.remove(n)
                except dnf.exceptions.PackagesNotInstalledError:
                        pass

        return names

def upgrade(dnfo, name):
        " :type dnfo: dnf.Base "
        if name == '*':
                dnfo.upgrade_all()
                return [name]

        if name.startswith('@'):
                dnfo.group_upgrade(name)
                return [name]

        q = dnfo.sack.query()
        a = q.available()
        i = q.installed()

        names = get_names(name)
        for n in names:
                i = i.filter(name=n)
                a = a.filter(name=n)
                if not i and a:
                        dnfo.install(n)
                else:
                        dnfo.upgrade(n)
        return names

def handle_state(dnfo, state, name):
        " :type dnfo: dnf.Base "
        if state in ['present', 'installed']:
                return install(dnfo, name)
        elif state in ['absent', 'removed']:
                return remove(dnfo, name)
        elif state == 'latest':
                return upgrade(dnfo, name)
        else:
                raise AnsibleDnfException()

def handle_list(dnfo, what):
        " :type dnfo: dnf.Base "
        if what == 'repos':
                return [k for (k, _) in dnfo.repos.iteritems()]
        else:
                q = dnfo.sack.query()
                installed = q.installed()
                updates = q.updates()
                available = q.available()
                #TODO: process these ^^
                return []
        

def parse_repolist(repos):
        if not repos:
                return []

        if repos == '*' or ',' not in repos:
                return [repos]

        return repos.split(',')

def main():
    # state=installed name=pkgspec
    # state=removed name=pkgspec
    # state=latest name=pkgspec
    #
    # informational commands:
    #   list=installed
    #   list=updates
    #   list=available
    #   list=repos
    #   list=pkgspec
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

    dnfo = init_dnf(
        repos = (enabled, disabled), 
        conf  = params['conf_file'], 
        gpg   = not params['disable_gpg_check']
    )

    if params['list']:
        ansible_result(module, 0, "", True, handle_list(dnfo, params['list']))
    else:
        pkg = params['name']
        state = params['state']

        names = handle_state(dnfo, state, pkg)

        dnfo.resolve()
        dnfo.download_packages(dnfo.transaction.install_set)
        dnfo.do_transaction()

        ansible_result(module, 0, "", True, ', '.join(names))

from ansible.module_utils.basic import *
if __name__ == '__main__':
        main()
