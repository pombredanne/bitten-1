# -*- coding: iso8859-1 -*-
#
# Copyright (C) 2005 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.cmlenz.net/wiki/License.

import os

def add_log_table(env, db):
    from bitten.model import BuildLog, BuildStep
    cursor = db.cursor()

    for table in BuildLog._schema:
        for stmt in db.to_sql(table):
            cursor.execute(stmt)

    cursor.execute("SELECT build,name,log FROM bitten_step "
                   "WHERE log IS NOT NULL")
    for build, step, log in cursor:
        build_log = BuildLog(env, build, step)
        build_log.messages = [(BuildLog.INFO, msg) for msg in log.splitlines()]
        build_log.insert(db)

    cursor.execute("CREATE TEMP TABLE old_step AS SELECT * FROM bitten_step")
    cursor.execute("DROP TABLE bitten_step")
    for table in BuildStep._schema:
        for stmt in db.to_sql(table):
            cursor.execute(stmt)
    cursor.execute("INSERT INTO bitten_step (build,name,description,status,"
                   "started,stopped) SELECT build,name,description,status,"
                   "started,stopped FROM old_step")

def add_recipe_to_config(env, db):
    from bitten.model import BuildConfig
    cursor = db.cursor()

    cursor.execute("CREATE TEMP TABLE old_config AS "
                   "SELECT * FROM bitten_config")
    cursor.execute("DROP TABLE bitten_config")
    for table in BuildConfig._schema:
        for stmt in db.to_sql(table):
            cursor.execute(stmt)
    cursor.execute("INSERT INTO bitten_config (name,path,active,recipe,min_rev,"
                   "max_rev,label,description) SELECT name,path,0,'',NULL,"
                   "NULL,label,description FROM old_config")

def add_config_to_reports(env, db):
    from bitten.model import Build
    try:
        from bsddb3 import db
        import dbxml
    except ImportError, e:
        return

    dbfile = os.path.join(env.path, 'db', 'bitten.dbxml')
    if not os.path.isfile(dbfile):
        return

    dbenv = db.DBEnv()
    dbenv.open(os.path.dirname(dbfile),
               db.DB_CREATE | db.DB_INIT_LOCK | db.DB_INIT_LOG |
               db.DB_INIT_MPOOL | db.DB_INIT_TXN, 0)

    mgr = dbxml.XmlManager(dbenv, 0)
    xtn = mgr.createTransaction()
    container = mgr.openContainer(dbfile)
    uc = mgr.createUpdateContext()

    container.addIndex(xtn, '', 'config', 'node-metadata-equality-string', uc)

    qc = mgr.createQueryContext()
    for value in mgr.query(xtn, 'collection("%s")/report' % dbfile, qc):
        doc = value.asDocument()
        metaval = dbxml.XmlValue()
        if doc.getMetaData('', 'build', metaval):
            build_id = int(metaval.asNumber())
            build = Build.fetch(env, id=build_id, db=db)
            if build:
                doc.setMetaData('', 'config', dbxml.XmlValue(build.config))
                container.updateDocument(xtn, doc, uc)
            else:
                # an orphaned report, for whatever reason... just remove it
                container.deleteDocument(xtn, doc, uc)

    xtn.commit()
    container.close()
    dbenv.close(0)

map = {
    2: [add_log_table],
    3: [add_recipe_to_config],
    4: [add_config_to_reports]
}
