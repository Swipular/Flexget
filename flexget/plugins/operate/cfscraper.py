from __future__ import unicode_literals, division, absolute_import
from builtins import *  # pylint: disable=unused-import, redefined-builtin

import logging
 
from flexget import plugin, validator
from flexget.event import event
from flexget.utils.requests import Session
 
log = logging.getLogger('cfscraper')

 
class CFScraper(object):
    """
    Plugin that enables scraping of cloudflare protected sites.

    Example::
      cfscraper: yes
    """
 
    def validator(self):
        return validator.factory('boolean')
 
    @plugin.priority(253)
    def on_task_start(self, task, config):
        try:
            import cfscrape
        except ImportError as e:
            log.debug('Error importing cfscrape: %s' % e)
            raise plugin.DependencyError('cfscraper', 'cfscrape', 'cfscrape module required. ImportError: %s' % e)

        class CFScrapeWrapper(Session, cfscrape.CloudflareScraper):
            """
            Wrapper class to strip unwanted input args from the Flexget requests class
            """

            def request(self, method, url, *args, **kwargs):
                return super(CFScrapeWrapper, self).request(method, url, *args, **kwargs)

        if config is True:
            task.requests = CFScrapeWrapper.create_scraper(task.requests)
 
 
@event('plugin.register')
def register_plugin():
    plugin.register(CFScraper, 'cfscraper', api_ver=2)
