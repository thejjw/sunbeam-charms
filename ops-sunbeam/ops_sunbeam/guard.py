# Copyright 2021 Canonical Ltd.
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

"""Module to handle errors and bailing out of an event/hook."""

import logging
import typing
from contextlib import (
    contextmanager,
)

from ops import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    StatusBase,
    WaitingStatus,
)

if typing.TYPE_CHECKING:
    from ops_sunbeam.charm import (
        OSBaseOperatorCharm,
    )

logger = logging.getLogger(__name__)


class GuardExceptionError(Exception):
    """GuardException."""

    pass


class BaseStatusExceptionError(Exception):
    """Charm is blocked."""

    status_type: type[StatusBase] = ActiveStatus

    def __init__(self, msg: str):
        super().__init__(msg)
        self.msg = msg

    def to_status(self):
        """Convert the exception to an ops status."""
        return self.status_type(self.msg)


class BlockedExceptionError(BaseStatusExceptionError):
    """Charm is blocked."""

    status_type = BlockedStatus


class MaintenanceExceptionError(BaseStatusExceptionError):
    """Charm is performing maintenance."""

    status_type = MaintenanceStatus


class WaitingExceptionError(BaseStatusExceptionError):
    """Charm is waiting."""

    status_type = WaitingStatus


@contextmanager
def guard(
    charm: "OSBaseOperatorCharm",
    section: str,
    handle_exception: bool = True,
    log_traceback: bool = True,
    **__,
) -> typing.Generator:
    """Context manager to handle errors and bailing out of an event/hook.

    The nature of Juju is that all the information may not be available to run
    a set of actions.  This context manager allows a section of code to be
    'guarded' so that it can be bailed at any time.

    It also handles errors which can be interpreted as a Block rather than the
    charm going into error.

    :param charm: the charm class (so that status can be set)
    :param section: the name of the section (for debugging/info purposes)
    :handle_exception: whether to handle the exception to a BlockedStatus()
    :log_traceback: whether to log the traceback for debugging purposes.
    :raises: Exception if handle_exception is False
    """
    logger.info("Entering guarded section: '%s'", section)
    try:
        yield
        logging.info("Completed guarded section fully: '%s'", section)
    except GuardExceptionError as e:
        logger.info(
            "Guarded Section: Early exit from '%s' due to '%s'.",
            section,
            str(e),
        )
    except BlockedExceptionError as e:
        logger.warning(
            "Charm is blocked in section '%s' due to '%s'", section, str(e)
        )
        charm.status.set(e.to_status())
    except WaitingExceptionError as e:
        logger.warning(
            "Charm is waiting in section '%s' due to '%s'", section, str(e)
        )
        charm.status.set(e.to_status())
    except MaintenanceExceptionError as e:
        logger.warning(
            "Charm performing maintenance in section '%s' due to '%s'",
            section,
            str(e),
        )
        charm.status.set(e.to_status())
    except Exception as e:
        # something else went wrong
        if handle_exception:
            logging.error(
                "Exception raised in section '%s': %s", section, str(e)
            )
            if log_traceback:
                import traceback

                logging.error(traceback.format_exc())
                charm.status.set(
                    BlockedStatus(
                        "Error in charm (see logs): {}".format(str(e))
                    )
                )
            return
        raise
