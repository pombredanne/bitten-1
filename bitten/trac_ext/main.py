# -*- coding: iso8859-1 -*-
#
# Copyright (C) 2005 Christopher Lenz <cmlenz@gmx.de>
#
# Bitten is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# Trac is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
#
# Author: Christopher Lenz <cmlenz@gmx.de>

import os.path

from trac.core import *
from trac.env import IEnvironmentSetupParticipant
from trac.perm import IPermissionRequestor
from bitten.model import Build, BuildConfig, schema_version
from bitten.trac_ext import web_ui

class BuildSystem(Component):

    implements(IEnvironmentSetupParticipant, IPermissionRequestor)

    # IEnvironmentSetupParticipant methods

    def environment_created(self):
        # Create the required tables
        db = self.env.get_db_cnx()
        cursor = db.cursor()
        for table in [Build._table, BuildConfig._table]:
            cursor.execute(db.to_sql(table))

        tarballs_dir = os.path.join(self.env.path, 'snapshots')

        cursor.execute("INSERT INTO system (name,value) "
                       "VALUES ('bitten_version',%s)", (schema_version,))
        db.commit()

    def environment_needs_upgrade(self, db):
        cursor = db.cursor()
        cursor.execute("SELECT value FROM system WHERE name='bitten_version'")
        row = cursor.fetchone()
        if not row or int(row[0]) < schema_version:
            return True

    def upgrade_environment(self, db):
        cursor = db.cursor()
        cursor.execute("SELECT value FROM system WHERE name='bitten_version'")
        row = cursor.fetchone()
        if not row:
            self.environment_created()
        else:
            current_version = int(row.fetchone()[0])
            for i in range(current_version + 1, schema_version + 1):
                name  = 'db%i' % i
                try:
                    upgrades = __import__('upgrades', globals(), locals(),
                                          [name])
                    script = getattr(upgrades, name)
                except AttributeError:
                    err = 'No upgrade module for version %i (%s.py)' % (i, name)
                    raise TracError, err
                script.do_upgrade(self.env, i, cursor)
            cursor.execute("UPDATE system SET value=%s WHERE "
                           "name='bitten_version'", (schema_version))
            self.log.info('Upgraded Bitten tables from version %d to %d',
                          current_version, schema_version)

    # IPermissionRequestor methods

    def get_permission_actions(self):
        actions = ['BUILD_VIEW', 'BUILD_CREATE', 'BUILD_MODIFY', 'BUILD_DELETE']
        return actions + [('BUILD_ADMIN', actions)]