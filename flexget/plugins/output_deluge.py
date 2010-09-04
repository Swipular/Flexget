from __future__ import with_statement
import logging
import time
import os
import base64
import urllib2
from flexget.plugin import *
from httplib import BadStatusLine

log = logging.getLogger('deluge')
        

class OutputDeluge(object):

    """
        Add the torrents directly to deluge, supporting custom save paths.
    """

    def validator(self):
        from flexget import validator
        root = validator.factory()
        root.accept('boolean')
        deluge = root.accept('dict')
        deluge.accept('text', key='host')
        deluge.accept('number', key='port')
        deluge.accept('text', key='user')
        deluge.accept('text', key='pass')
        deluge.accept('path', key='path', allow_replacement=True)
        deluge.accept('path', key='movedone', allow_replacement=True)
        deluge.accept('text', key='label')
        deluge.accept('boolean', key='queuetotop')
        deluge.accept('boolean', key='automanaged')
        deluge.accept('decimal', key='maxupspeed')
        deluge.accept('decimal', key='maxdownspeed')
        deluge.accept('number', key='maxconnections')
        deluge.accept('number', key='maxupslots')
        deluge.accept('decimal', key='ratio')
        deluge.accept('boolean', key='removeatratio')
        deluge.accept('boolean', key='addpaused')
        deluge.accept('boolean', key='compact')
        deluge.accept('text', key='content_filename')
        deluge.accept('boolean', key='enabled')
        return root

    def get_config(self, feed):
        config = feed.config.get('deluge', {})
        if isinstance(config, bool):
            config = {'enabled': config}
        config.setdefault('host', 'localhost')
        config.setdefault('port', 58846)
        config.setdefault('user', '')
        config.setdefault('pass', '')
        config.setdefault('enabled', True)
        config.setdefault('path', '')
        config.setdefault('movedone', '')
        config.setdefault('label', '')
        return config

    def __init__(self):
        self.deluge12 = False
        self.reactorRunning = 0
        self.options = {'maxupspeed': 'max_upload_speed', 'maxdownspeed': 'max_download_speed', \
            'maxconnections': 'max_connections', 'maxupslots': 'max_upload_slots', \
            'automanaged': 'auto_managed', 'ratio': 'stop_ratio', 'removeatratio': 'remove_at_ratio', \
            'addpaused': 'add_paused', 'compact': 'compact_allocation'}

    @priority(120)
    def on_process_start(self, feed):
        """
            Register the usable set: keywords. Detect what version of deluge is loaded.
        """
        set_plugin = get_plugin_by_name('set')
        set_plugin.instance.register_keys({'path': 'text', 'movedone': 'text', \
            'queuetotop': 'boolean', 'label': 'text', 'automanaged': 'boolean', \
            'maxupspeed': 'decimal', 'maxdownspeed': 'decimal', 'maxupslots': 'number', \
            'maxconnections': 'number', 'ratio': 'decimal', 'removeatratio': 'boolean', \
            'addpaused': 'boolean', 'compact': 'boolean', 'content_filename': 'text'})
        if not self.deluge12:
            logger = log.info if feed.manager.options.test else log.debug
            try:
                log.debug("Testing for deluge 1.1 API")
                from deluge.ui.client import sclient
                log.debug("1.1 API found")
            except:
                log.debug("Testing for deluge 1.2 API")
                try:
                    from deluge.ui.client import client
                except ImportError, e:
                    raise PluginError('Deluge module and it\'s dependencies required. ImportError: %s' % e, log)
                try:
                    from twisted.internet import reactor
                except:
                    raise PluginError('Twisted module required', log)
                logger("Using deluge 1.2 api")
                self.deluge12 = True
            else:
                logger("Using deluge 1.1 api")
                self.deluge12 = False

    @priority(120)
    def on_feed_download(self, feed):
        """
            call download plugin to generate the temp files we will load into deluge
            then verify they are valid torrents
        """
        import deluge.ui.common
        config = self.get_config(feed)
        if not config['enabled']:
            return
        #If the download plugin is not enabled, we need to call it to get our temp .torrent files
        if not 'download' in feed.config:
            download = get_plugin_by_name('download')
            download.instance.get_temp_files(feed)

        #Check torrent files are valid
        for entry in feed.accepted:
            if os.path.exists(entry.get('file', '')):
                #Check if downloaded file is a valid torrent file
                try:
                    info = deluge.ui.common.TorrentInfo(entry['file'])
                except Exception, e:
                    feed.fail(entry, 'Invalid torrent file')
                    log.error("Torrent file appears invalid for: %s", entry['title'])
                    #clean up invalid torrent file
                    os.remove(entry['file'])
                    del(entry['file'])

    @priority(135)
    def on_feed_output(self, feed):
        """Add torrents to deluge at exit."""
        config = self.get_config(feed)
        # don't add when learning
        if feed.manager.options.learn:
            return
        if not config['enabled'] or not (feed.accepted or feed.manager.options.test):
            return

        add_to_deluge = self.add_to_deluge12 if self.deluge12 else self.add_to_deluge11
        add_to_deluge(feed, config)

    def add_to_deluge11(self, feed, config):
        """ Add torrents to deluge using deluge 1.1.x api. """
        try:
            from deluge.ui.client import sclient
        except:
            raise PluginError('Deluge module required', log)

        sclient.set_core_uri()
        for entry in feed.accepted:
            try:
                before = sclient.get_session_state()
            except Exception, (errno, msg):
                raise PluginError('Could not communicate with deluge core. %s' % msg, log)
            if feed.manager.options.test:
                return
            opts = {}
            path = entry.get('path', config['path'])
            if path:
                opts['download_location'] = os.path.expanduser(path % entry)
            for fopt, dopt in self.options.iteritems():
                value = entry.get(fopt, config.get(fopt))
                if value != None:
                    opts[dopt] = value
                    if fopt == 'ratio':
                        opts['stop_at_ratio'] = True
            
            # check that file is downloaded
            if not 'file' in entry:
                feed.fail(entry, 'file missing?')
                continue
                
            # see that temp file is present
            if not os.path.exists(entry['file']):
                tmp_path = os.path.join(feed.manager.config_base, 'temp')
                log.debug('entry: %s' % entry)
                log.debug('temp: %s' % ', '.join(os.listdir(tmp_path)))
                feed.fail(entry, "Downloaded temp file '%s' doesn't exist!?" % entry['file'])
                continue

            sclient.add_torrent_file([entry['file']], [opts])
            log.info("%s torrent added to deluge with options %s" % (entry['title'], opts))
            # clean up temp file if download plugin is not configured for this feed
            if not 'download' in feed.config:
                os.remove(entry['file'])
                del(entry['file'])
                
            movedone = entry.get('movedone', config['movedone'])
            label = entry.get('label', config['label']).lower()
            queuetotop = entry.get('queuetotop', config.get('queuetotop'))

            # Sometimes deluge takes a moment to add the torrent, wait a second.
            time.sleep(2)
            after = sclient.get_session_state()
            for item in after:
                # find torrentid of just added torrent
                if not item in before:
                    # entry['deluge_torrentid'] = item
                    if movedone:
                        if not os.path.isdir(os.path.expanduser(movedone % entry)):
                            log.debug("movedone path %s doesn't exist, creating" % (movedone % entry))
                            os.makedirs(os.path.expanduser(movedone % entry))
                        log.debug("%s move on complete set to %s" % (entry['title'], movedone % entry))
                        sclient.set_torrent_move_on_completed(item, True)
                        sclient.set_torrent_move_on_completed_path(item, os.path.expanduser(movedone % entry))
                    if label:
                        if not "label" in sclient.get_enabled_plugins():
                            sclient.enable_plugin("label")
                        if not label in sclient.label_get_labels():
                            sclient.label_add(label)
                        log.debug("%s label set to '%s'" % (entry['title'], label))
                        sclient.label_set_torrent(item, label)
                    if queuetotop:
                        log.debug("%s moved to top of queue" % entry['title'])
                        sclient.queue_top([item])
                    break
            else:
                log.info("%s is already loaded in deluge. Cannot change label, movedone, or queuetotop" % entry['title'])                
                
    def add_to_deluge12(self, feed, config):
    
        """ This is the new add to deluge method, using reactor.iterate """
        
        from deluge.ui.client import client
        from twisted.internet import reactor, defer
        from twisted.internet.task import deferLater
        
        def start_reactor():
            #if this is the first this function is being called, we have to call startRunning
            if self.reactorRunning < 2:
                reactor.startRunning(True)
            self.reactorRunning = 1
            while self.reactorRunning == 1:
                reactor.iterate()
            #if there was an error requiring an exception during reactor running, it should be
            #   thrown here so the reactor loop doesn't exit prematurely
            if self.reactorRunning < 0:
                self.reactorRunning = 2
                raise PluginError('Could not connect to deluge daemon', log)
            self.reactorRunning = 2
                
        def pause_reactor(result):
            self.reactorRunning = result

        def on_connect_success(result, feed):
            if not result:
                # TODO: connect failed? do something
                log.debug("on_connect_success returned a failed result. BUG?")

            if feed.manager.options.test:
                log.debug('Test connection to deluge daemon successful.')
                client.disconnect()
                return
                
            def on_success(torrent_id, entry, opts):
                dlist = []
                if not torrent_id:
                    log.info("%s is already loaded in deluge, cannot set options." % entry['title'])
                    return
                log.info("%s successfully added to deluge." % entry['title'])
                if opts['movedone']:
                    if client.is_localhost():
                        if not os.path.isdir(opts['movedone']) and False:
                            log.debug("movedone path %s doesn't exist, creating" % opts['movedone'])
                            os.makedirs(opts['movedone'])
                    else:
                        log.warning("If movedone path does not exist on the machine running the daemon, movedone will fail.")
                    dlist.append(client.core.set_torrent_move_completed(torrent_id, True))
                    dlist.append(client.core.set_torrent_move_completed_path(torrent_id, opts['movedone']))
                    log.debug("%s move on complete set to %s" % (entry['title'], opts['movedone']))
                if opts['label']:
                    dlist.append(client.label.set_torrent(torrent_id, opts['label']))
                if 'queuetotop' in opts:
                    if opts['queuetotop']:
                        dlist.append(client.core.queue_top([torrent_id]))
                        log.debug("%s moved to top of queue" % entry['title'])
                    else:
                        dlist.append(client.core.queue_bottom([torrent_id]))
                        log.debug("%s moved to bottom of queue" % entry['title'])
                if opts.get('content_filename'):

                    def on_get_torrent_status(status):
                        for file in status['files']:
                            # Only rename file if it is > 90% of the content
                            if file['size'] > (status['total_size'] * 0.9):
                                filename = opts['content_filename'] + os.path.splitext(file['path'])[1]
                                counter = 1

                                def file_exists():
                                    # Checks the download path as well as the move completed path for existence of the file
                                    if os.path.exists(os.path.join(status['save_path'], filename)):
                                        return True
                                    elif status.get('move_on_completed') and status.get('move_on_completed_path'):
                                        if os.path.exists(os.path.join(status['move_on_completed_path'], filename)):
                                            return True
                                    else:
                                        return False

                                if client.is_localhost():
                                    while file_exists():
                                        # Try appending a (#) suffix till a unique filename is found
                                        filename = ''.join([opts['content_filename'], '(', str(counter), ')', os.path.splitext(file['path'])[1]])
                                        counter += 1
                                else:
                                    log.debug("Cannot ensure content_filename is unique when adding to a remote deluge daemon.")
                                log.debug("File %s in %s renamed to %s" % (file['path'], entry['title'], filename))
                                return client.core.rename_files(torrent_id, [(file['index'], filename)])
                        else:
                            log.warning("No files in %s are > 90% of content size, no files renamed." % entry['title'])

                    status_keys = ['files', 'total_size', 'save_path', 'move_on_completed_path', 'move_on_completed']
                    dlist.append(client.core.get_torrent_status(torrent_id, status_keys).addCallback(on_get_torrent_status))

                return defer.DeferredList(dlist)

            def on_fail(result, feed, entry):
                log.info("%s was not added to deluge! %s" % (entry['title'], result))
                feed.fail(entry, "Could not be added to deluge")
            
            # dlist is a list of deferreds that must complete before we exit
            dlist = []
            # loop through entries to get a list of labels to add
            labels = set([entry['label'].lower() for entry in feed.accepted if entry.get('label')])
            if config.get('label'):
                labels.add(config['label'].lower())
            label_deferred = defer.succeed(True)
            if labels:
                # Make sure the label plugin is available and enabled, then add appropriate labels

                def on_get_enabled_plugins(plugins):

                    def on_label_enabled(result):
                        """ This runs when we verify the label plugin is enabled. """

                        def on_get_labels(d_labels):
                            dlist = []
                            for label in labels:
                                if not label in d_labels:
                                    log.debug("Adding the label %s to deluge" % label)
                                    dlist.append(client.label.add(label))
                            return defer.DeferredList(dlist)

                        return client.label.get_labels().addCallback(on_get_labels)

                    if 'Label' in plugins:
                        return on_label_enabled(True)
                    else:

                        def on_get_available_plugins(plugins):
                            if 'Label' in plugins:
                                log.debug("Enabling label plugin in deluge")
                                return client.core.enable_plugin('Label').addCallback(on_label_enabled)
                            else:
                                log.error("Label plugin is not installed in deluge")

                        return client.core.get_available_plugins().addCallback(on_get_available_plugins)

                label_deferred = client.core.get_enabled_plugins().addCallback(on_get_enabled_plugins)
                dlist.append(label_deferred)

            # add the torrents
            for entry in feed.accepted:
                magnet, filedump = None, None
                if entry.get('url', '').startswith('magnet:'):
                    magnet = entry['url']
                else:
                    if not os.path.exists(entry['file']):
                        feed.fail(entry, "Downloaded temp file '%s' doesn't exist!" % entry['file'])
                        del(entry['file'])
                        continue
                    try:
                        f = open(entry['file'], 'rb')
                        filedump = base64.encodestring(f.read())
                    finally:
                        f.close()
                path = ''
                try:
                    path = os.path.expanduser(entry.get('path', config['path']) % entry)
                except KeyError, e:
                    log.error("Could not set path for %s: does not contain the field '%s.'" % (entry['title'], e))
                opts = {}
                if path:
                    opts['download_location'] = path
                for fopt, dopt in self.options.iteritems():
                    value = entry.get(fopt, config.get(fopt))
                    if value != None:
                        opts[dopt] = value
                        if fopt == 'ratio':
                            opts['stop_at_ratio'] = True

                def on_add_torrent(result, title, filedump, opts, magnet=False):
                    log.debug("Adding %s to deluge." % title)
                    if magnet:
                        return client.core.add_torrent_magnet(magnet, opts)
                    else:
                        return client.core.add_torrent_file(title, filedump, opts)

                # Make sure our labels have been added before adding the torrents
                addresult = label_deferred.addCallback(on_add_torrent, entry['title'], filedump, opts, magnet)

                # clean up temp file if download plugin is not configured for this feed
                if not magnet and not 'download' in feed.config:
                    os.remove(entry['file'])
                    del(entry['file'])

                # Make a new set of options, that get set after the torrent has been added
                opts = {}
                try:
                    opts['movedone'] = os.path.expanduser(entry.get('movedone', config['movedone']) % entry)
                except KeyError, e:
                    log.error("Could not set movedone for %s: does not contain the field '%s.'" % (entry['title'], e))
                    opts['movedone'] = ''
                opts['label'] = entry.get('label', config['label']).lower()
                opts['queuetotop'] = entry.get('queuetotop', config.get('queuetotop'))
                try:
                    opts['content_filename'] = entry.get('content_filename', config.get('content_filename', '')) % entry
                except KeyError, e:
                    log.error("Could not set content_filename for %s: does not contain the field '%s.'" % (entry['title'], e))
                    opts['content_filename'] = ''

                addresult.addCallbacks(on_success, on_fail, callbackArgs=(entry, opts), errbackArgs=(feed, entry))
                dlist.append(addresult)
                
            def on_complete(result):
                client.disconnect()
            tasks = defer.DeferredList(dlist).addBoth(on_complete)

            # Schedule a disconnect to happen in 30 seconds if FlexGet hangs while connected to Deluge
            def on_timeout(result):
                log.error('Timed out while adding torrents to deluge.')
                log.debug('dlist: %s' % result.resultList)
                client.disconnect()
            reactor.callLater(30, lambda: tasks.called or on_timeout(tasks))
            
        def on_connect_fail(result, feed):
            log.info('connect failed result: %s' % result)
            # clean up temp file if download plugin is not configured for this feed
            if not 'download' in feed.config:
                for entry in feed.accepted:
                    if os.path.exists(entry.get('file', '')):
                        os.remove(entry['file'])
                        del(entry['file'])
            log.debug('Connect to deluge daemon failed, result: %s' % result)
            reactor.callLater(0, pause_reactor, -1)

        def on_disconnect():
            # pause the reactor when we get disconnected from the daemon, so flexget can continue
            reactor.callLater(0, pause_reactor, 0)

        client.set_disconnect_callback(on_disconnect)

        d = client.connect(
            host=config['host'],
            port=config['port'],
            username=config['user'],
            password=config['pass'])

        d.addCallback(on_connect_success, feed).addErrback(on_connect_fail, feed)
        start_reactor()
        
    def on_process_end(self, feed):
        if self.deluge12 and self.reactorRunning == 2:
            from twisted.internet import reactor
            reactor.fireSystemEvent('shutdown')
            self.reactorRunning = 0

register_plugin(OutputDeluge, 'deluge')
