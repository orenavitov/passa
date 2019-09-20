# -*- coding=utf-8 -*-

from __future__ import absolute_import, print_function, unicode_literals

from ..actions.remove import remove
from ._base import BaseCommand
from .options import dev_group, clean_group, sync_group, packages


class Command(BaseCommand):

    name = "remove"
    description = "Remove packages from project."
    arguments = [dev_group, clean_group, sync_group, packages]

    def run(self, options):
        return remove(project=options.project, only=options.only,
                      packages=options.packages, clean=options.clean, sync=options.sync)


if __name__ == "__main__":
    Command.run_parser()
