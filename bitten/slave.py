# -*- coding: utf-8 -*-
#
# Copyright (C) 2007 Edgewall Software
# Copyright (C) 2005-2007 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.edgewall.org/wiki/License.

"""Implementation of the build slave."""

from datetime import datetime
import urllib2
import logging
import os
import platform
import shutil
import tempfile
import time

from bitten.build import BuildError
from bitten.build.config import Configuration
from bitten.recipe import Recipe
from bitten.util import xmlio

__all__ = ['BuildSlave', 'ExitSlave']
__docformat__ = 'restructuredtext en'

log = logging.getLogger('bitten.slave')


class SaneHTTPErrorProcessor(urllib2.HTTPErrorProcessor):
    "The HTTPErrorProcessor defined in urllib needs some love."

    def http_response(self, request, response):
        code, msg, hdrs = response.code, response.msg, response.info()
        if code >= 300:
            response = self.parent.error(
                'http', request, response, code, msg, hdrs)
        return response


class SaneHTTPRequest(urllib2.Request):

    def __init__(self, method, url, data=None, headers={}):
        urllib2.Request.__init__(self, url, data, headers)
        self.method = method

    def get_method(self):
        if self.method is None:
            self.method = self.has_data() and 'POST' or 'GET'
        return self.method


class BuildSlave(object):
    """BEEP initiator implementation for the build slave."""

    def __init__(self, url, name=None, config=None, dry_run=False,
                 work_dir=None, keep_files=False, single_build=False,
                 username=None, password=None, dump_reports=False):
        """Create the build slave instance.
        
        :param url: the URL of the build master, or the path to a build recipe
                    file
        :param name: the name with which this slave should identify itself
        :param config: the path to the slave configuration file
        :param dry_run: wether the build outcome should not be reported back
                        to the master
        :param work_dir: the working directory to use for build execution
        :param keep_files: whether files and directories created for build
                           execution should be kept when done
        :param single_build: whether this slave should exit after completing a 
                             single build, or continue processing builds forever
        :param username: the username to use when authentication against the
                         build master is requested
        :param password: the password to use when authentication is needed
        :param dump_reports: whether report data should be written to the
                             standard output, in addition to being transmitted
                             to the build master
        """
        self.url = url
        self.local = not url.startswith('http') and not url.startswith('https')
        if name is None:
            name = platform.node().split('.', 1)[0].lower()
        self.name = name
        self.config = Configuration(config)
        self.dry_run = dry_run
        if not work_dir:
            work_dir = tempfile.mkdtemp(prefix='bitten')
        elif not os.path.exists(work_dir):
            os.makedirs(work_dir)
        self.work_dir = work_dir
        self.keep_files = keep_files
        self.single_build = single_build
        self.dump_reports = dump_reports

        if not self.local:
            self.opener = urllib2.build_opener(SaneHTTPErrorProcessor)
            password_mgr = urllib2.HTTPPasswordMgrWithDefaultRealm()
            if username and password:
                password_mgr.add_password(None, url, username, password)
            self.opener.add_handler(urllib2.HTTPBasicAuthHandler(password_mgr))
            self.opener.add_handler(urllib2.HTTPDigestAuthHandler(password_mgr))

    def request(self, method, url, body=None, headers=None):
        log.debug('Sending %s request to %r', method, url)
        req = SaneHTTPRequest(method, url, body, headers or {})
        try:
            return self.opener.open(req)
        except urllib2.HTTPError, e:
            if e.code >= 300:
                log.warning('Server returned error %d: %s', e.code, e.msg)
                raise
            return e

    def run(self):
        if self.local:
            fileobj = open(self.url)
            try:
                self._execute_build(None, fileobj)
            finally:
                fileobj.close()
            return

        while True:
            try:
                try:
                    self._create_build()
                except urllib2.URLError, e:
                    log.error(e)
                    raise ExitSlave()
            except ExitSlave:
                break
            time.sleep(30)

    def quit(self):
        log.info('Shutting down')
        raise ExitSlave()

    def _create_build(self):
        xml = xmlio.Element('slave', name=self.name)[
            xmlio.Element('platform', processor=self.config['processor'])[
                self.config['machine']
            ],
            xmlio.Element('os', family=self.config['family'],
                                version=self.config['version'])[
                self.config['os']
            ],
        ]

        log.debug('Configured packages: %s', self.config.packages)
        for package, properties in self.config.packages.items():
            xml.append(xmlio.Element('package', name=package, **properties))

        body = str(xml)
        log.debug('Sending slave configuration: %s', body)
        resp = self.request('POST', self.url, body, {
            'Content-Length': len(body),
            'Content-Type': 'application/x-bitten+xml'
        })

        if resp.code == 201:
            self._initiate_build(resp.info().get('location'))
        elif resp.code == 204:
            log.info('No pending builds')
        else:
            log.error('Unexpected response (%d %s)', resp.code, resp.msg)
            raise ExitSlave()

    def _initiate_build(self, build_url):
        log.info('Build pending at %s', build_url)
        try:
            resp = self.request('GET', build_url)
            if resp.code == 200:
                self._execute_build(build_url, resp)
            else:
                log.error('Unexpected response (%d): %s', resp.code, resp.msg)
                self._cancel_build(build_url)
        except KeyboardInterrupt:
            log.warning('Build interrupted')
            self._cancel_build(build_url)

    def _execute_build(self, build_url, fileobj):
        build_id = build_url and int(build_url.split('/')[-1]) or 0
        xml = xmlio.parse(fileobj)
        basedir = os.path.join(self.work_dir, 'build_%d' % build_id)
        if not os.path.exists(basedir):
            os.mkdir(basedir)
        try:
            recipe = Recipe(xml, basedir, self.config)
            for step in recipe:
                log.info('Executing build step %r', step.id)
                if not self._execute_step(build_url, recipe, step):
                    log.warning('Stopping build due to failure')
                    break
            else:
                log.info('Build completed')
                if self.dry_run:
                    self._cancel_build(build_url)
        finally:
            if not self.keep_files:
                log.debug('Removing build directory %s' % basedir)
                shutil.rmtree(basedir)
            if self.single_build:
                log.info('Exiting after single build completed.')
                raise ExitSlave()

    def _execute_step(self, build_url, recipe, step):
        failed = False
        started = datetime.utcnow()
        xml = xmlio.Element('result', step=step.id, time=started.isoformat())
        try:
            for type, category, generator, output in \
                    step.execute(recipe.ctxt):
                if type == Recipe.ERROR:
                    failed = True
                if type == Recipe.REPORT and self.dump_reports:
                    print output
                xml.append(xmlio.Element(type, category=category,
                                         generator=generator)[
                    output
                ])
        except KeyboardInterrupt:
            log.warning('Build interrupted')
            self._cancel_build(build_url)
        except BuildError, e:
            log.error('Build step %r failed (%s)', step.id, e)
            failed = True
        except Exception, e:
            log.error('Internal error in build step %r', step.id, exc_info=True)
            failed = True
        xml.attr['duration'] = (datetime.utcnow() - started).seconds
        if failed:
            xml.attr['status'] = 'failure'
            log.warning('Build step %r failed', step.id)
        else:
            xml.attr['status'] = 'success'
            log.info('Build step %s completed successfully', step.id)

        if not self.local and not self.dry_run:
            try:
                resp = self.request('POST', build_url + '/steps/', str(xml), {
                    'Content-Type': 'application/x-bitten+xml'
                })
                if resp.code != 201:
                    log.error('Unexpected response (%d): %s', resp.code,
                              resp.msg)
            except KeyboardInterrupt:
                log.warning('Build interrupted')
                self._cancel_build(build_url)

        return not failed or step.onerror != 'fail'

    def _cancel_build(self, build_url):
        log.info('Cancelling build at %s', build_url)
        if not self.local:
            resp = self.request('DELETE', build_url)
            if resp.code not in (200, 204):
                log.error('Unexpected response (%d): %s', resp.code, resp.msg)
        raise ExitSlave()


class ExitSlave(Exception):
    """Exception used internally by the slave to signal that the slave process
    should be stopped.
    """


def main():
    """Main entry point for running the build slave."""
    from bitten import __version__ as VERSION
    from optparse import OptionParser

    parser = OptionParser(usage='usage: %prog [options] url',
                          version='%%prog %s' % VERSION)
    parser.add_option('--name', action='store', dest='name',
                      help='name of this slave (defaults to host name)')
    parser.add_option('-f', '--config', action='store', dest='config',
                      metavar='FILE', help='path to configuration file')
    parser.add_option('-u', '--user', dest='username',
                      help='the username to use for authentication')
    parser.add_option('-p', '--password', dest='password',
                      help='the password to use when authenticating')

    group = parser.add_option_group('building')
    group.add_option('-d', '--work-dir', action='store', dest='work_dir',
                     metavar='DIR', help='working directory for builds')
    group.add_option('-k', '--keep-files', action='store_true',
                     dest='keep_files', 
                     help='don\'t delete files after builds')
    group.add_option('-s', '--single', action='store_true',
                     dest='single_build',
                     help='exit after completing a single build')
    group.add_option('-n', '--dry-run', action='store_true', dest='dry_run',
                     help='don\'t report results back to master')

    group = parser.add_option_group('logging')
    group.add_option('-l', '--log', dest='logfile', metavar='FILENAME',
                     help='write log messages to FILENAME')
    group.add_option('-v', '--verbose', action='store_const', dest='loglevel',
                     const=logging.DEBUG, help='print as much as possible')
    group.add_option('-q', '--quiet', action='store_const', dest='loglevel',
                     const=logging.WARN, help='print as little as possible')
    group.add_option('--dump-reports', action='store_true', dest='dump_reports',
                     help='whether report data should be printed')

    parser.set_defaults(dry_run=False, keep_files=False,
                        loglevel=logging.INFO, single_build=False,
                        dump_reports=False)
    options, args = parser.parse_args()

    if len(args) < 1:
        parser.error('incorrect number of arguments')
    url = args[0]

    logger = logging.getLogger('bitten')
    logger.setLevel(options.loglevel)
    handler = logging.StreamHandler()
    handler.setLevel(options.loglevel)
    formatter = logging.Formatter('[%(levelname)-8s] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    if options.logfile:
        handler = logging.FileHandler(options.logfile)
        handler.setLevel(options.loglevel)
        formatter = logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: '
                                      '%(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    slave = BuildSlave(url, name=options.name, config=options.config,
                       dry_run=options.dry_run, work_dir=options.work_dir,
                       keep_files=options.keep_files,
                       single_build=options.single_build,
                       username=options.username, password=options.password,
                       dump_reports=options.dump_reports)
    try:
        try:
            slave.run()
        except KeyboardInterrupt:
            slave.quit()
    except ExitSlave:
        pass

    if not options.work_dir:
        log.debug('Removing temporary directory %s' % slave.work_dir)
        shutil.rmtree(slave.work_dir)

if __name__ == '__main__':
    main()
