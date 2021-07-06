# Copyright 2021, Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from ops import framework
import logging

log = logging.getLogger(__name__)


class ContextGenerator(framework.Object):
    """Base class for all context generators"""
    interfaces = []
    related = False
    complete = False
    missing_data = []

    def __init__(self, charm, context_name):
        super().__init__(charm, context_name)
        self.charm = charm

    def __call__(self):
        raise NotImplementedError

    def context_complete(self, ctxt):
        """Check for missing data for the required context data.
        Set self.missing_data if it exists and return False.
        Set self.complete if no missing data and return True.
        """
        # Fresh start
        self.complete = False
        self.missing_data = []
        for k, v in ctxt.items(ctxt):
            if v is None or v == '':
                if k not in self.missing_data:
                    self.missing_data.append(k)

        if self.missing_data:
            self.complete = False
            log.debug(f"Missing required data: {' '.join(self.missing_data)}")
        else:
            self.complete = True
        return self.complete


class DatabaseContext(ContextGenerator):

    def __init__(self, charm, relation_name):
        super().__init__(charm, 'database_context')
        self.relation_name = relation_name

    def __call__(self):
        relation = self.charm.model.get_relation(self.relation_name)

        return {}


class WSGIWorkerConfigContext(ContextGenerator):

    def __init__(self, charm):
        super().__init__(charm, 'WSGIWorkerConfigContext')

    def __call__(self, *args, **kwargs):
        return {
            'name': 'keystone',
            'admin_script': '/usr/bin/keystone-wsgi-admin',
            'public_script': '/usr/bin/keystone/wsgi-public',
        }


class KeystoneContext(ContextGenerator):

    def __init__(self, charm):
        super().__init__(charm, 'KeystoneContext')

    def __call__(self, *args, **kwargs):
        ctxt = {
            'api_version': 3,
            'admin_role': self.charm.model.config['admin-role'],
        }

        return ctxt
