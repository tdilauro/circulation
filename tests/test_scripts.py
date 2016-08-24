import datetime
import os
import tempfile

from nose.tools import (
    assert_raises,
    eq_,
    set_trace,
)

from . import (
    DatabaseTest,
)
from model import (
    get_one,
    CustomList,
    DataSource,
    Identifier,
    Timestamp
)
from scripts import (
    Script,
    CustomListManagementScript,
    DatabaseMigrationScript,
    IdentifierInputScript,
    RunCoverageProviderScript,
    WorkProcessingScript,
    MockStdin,
)
from util.opds_writer import (
    OPDSFeed,
)

class TestScript(DatabaseTest):

    def test_parse_time(self): 
        reference_date = datetime.datetime(2016, 1, 1)

        eq_(Script.parse_time("2016-01-01"), reference_date)

        eq_(Script.parse_time("2016-1-1"), reference_date)

        eq_(Script.parse_time("1/1/2016"), reference_date)

        eq_(Script.parse_time("20160101"), reference_date)

        assert_raises(ValueError, Script.parse_time, "201601-01")


class TestIdentifierInputScript(DatabaseTest):

    def test_parse_list_as_identifiers(self):

        i1 = self._identifier()
        i2 = self._identifier()
        args = [i1.identifier, 'no-such-identifier', i2.identifier]
        identifiers = IdentifierInputScript.parse_identifier_list(
            self._db, i1.type, args
        )
        eq_([i1, i2], identifiers)

        eq_([], IdentifierInputScript.parse_identifier_list(
            self._db, i1.type, [])
        )

    def test_parse_list_as_identifiers_with_autocreate(self):

        type = Identifier.OVERDRIVE_ID
        args = ['brand-new-identifier']
        [i] = IdentifierInputScript.parse_identifier_list(
            self._db, type, args, autocreate=True
        )
        eq_(type, i.type)
        eq_('brand-new-identifier', i.identifier)

    def test_parse_command_line(self):
        i1 = self._identifier()
        i2 = self._identifier()
        # We pass in one identifier on the command line...
        cmd_args = ["--identifier-type",
                    i1.type, i1.identifier]
        # ...and another one into standard input.
        stdin = MockStdin(i2.identifier)
        parsed = IdentifierInputScript.parse_command_line(
            self._db, cmd_args, stdin
        )
        eq_([i1, i2], parsed.identifiers)
        eq_(i1.type, parsed.identifier_type)

    def test_parse_command_line_no_identifiers(self):
        cmd_args = ["--identifier-type", Identifier.OVERDRIVE_ID]
        parsed = IdentifierInputScript.parse_command_line(
            self._db, cmd_args, MockStdin()
        )
        eq_([], parsed.identifiers)
        eq_(Identifier.OVERDRIVE_ID, parsed.identifier_type)


class TestRunCoverageProviderScript(DatabaseTest):

    def test_parse_command_line(self):
        identifier = self._identifier()
        cmd_args = ["--cutoff-time", "2016-05-01", "--identifier-type", 
                    identifier.type, identifier.identifier]
        parsed = RunCoverageProviderScript.parse_command_line(
            self._db, cmd_args, MockStdin()
        )
        eq_(datetime.datetime(2016, 5, 1), parsed.cutoff_time)
        eq_([identifier], parsed.identifiers)
        eq_(identifier.type, parsed.identifier_type)

        
class TestWorkProcessingScript(DatabaseTest):

    def test_make_query(self):
        # Create two Gutenberg works and one Overdrive work
        g1 = self._work(with_license_pool=True, with_open_access_download=True)
        g2 = self._work(with_license_pool=True, with_open_access_download=True)

        overdrive_edition, overdrive_pool = self._edition(
            data_source_name=DataSource.OVERDRIVE, 
            identifier_type=Identifier.OVERDRIVE_ID,
            with_license_pool=True
        )
        overdrive_work = self._work(presentation_edition=overdrive_edition)

        everything = WorkProcessingScript.make_query(self._db, None, None)
        eq_(set([g1, g2, overdrive_work]), set(everything.all()))

        all_gutenberg = WorkProcessingScript.make_query(
            self._db, Identifier.GUTENBERG_ID, []
        )
        eq_(set([g1, g2]), set(all_gutenberg.all()))

        one_gutenberg = WorkProcessingScript.make_query(
            self._db, Identifier.GUTENBERG_ID, [g1.license_pools[0].identifier]
        )
        eq_([g1], one_gutenberg.all())


class TestDatabaseMigrationScript(DatabaseTest):

    def _create_test_migrations(self):
        """Sets up migrations in the expected locations"""

        directories = self.script.directories_by_priority
        [self.core_migration_dir, self.parent_migration_dir] = directories

        # Create temporary migration directories where
        # DatabaseMigrationScript expects them.
        for migration_dir in directories:
            if not os.path.isdir(migration_dir):
                temp_migration_dir = tempfile.mkdtemp()
                os.rename(temp_migration_dir, migration_dir)

        self._create_test_migration_file(self.core_migration_dir, 'CORE', 'sql')
        self._create_test_migration_file(self.core_migration_dir, 'CORE', 'py')
        self._create_test_migration_file(self.parent_migration_dir, 'SERVER', 'sql')
        self._create_test_migration_file(self.parent_migration_dir, 'SERVER', 'py')

    def _create_test_migration_file(self, directory, unique_string,
                                    migration_type, migration_date=None):
        suffix = '.'+migration_type

        if migration_type=='sql':
            # Create content for a SQL file.
            service = "Test Database Migration Script - %s" % unique_string
            content = (("insert into timestamps(service, timestamp)"
                        " values ('%s', '%s');") % (service, '1970-01-01'))
        elif migration_type=='py':
            # Create content for a Python file.
            core = os.path.split(self.core_migration_dir)[0]
            target_dir = os.path.join(core, 'tests')
            content = (
                "import tempfile\nimport os\n\n"+
                "file_info = tempfile.mkstemp(prefix='"+
                unique_string+"-', suffix='.py', dir='"+target_dir+"')\n\n"+
                "# Close file descriptor\n"+
                "os.close(file_info[0])\n"
            )

        if not migration_date:
            migration_date = '20260811'
        prefix = migration_date + '-'
        migration_file_info = tempfile.mkstemp(
            prefix=prefix, suffix=suffix, dir=directory
        )
        self.migration_files.append(migration_file_info)
        with open(migration_file_info[1], 'w') as migration:
            migration.write(content)

    def setup(self):
        super(TestDatabaseMigrationScript, self).setup()
        self.script = DatabaseMigrationScript(_db=self._db)

        # This list holds any temporary files created during tests
        # so they can be deleted during teardown().
        self.migration_files = []
        self._create_test_migrations()

        stamp = datetime.datetime.strptime('20260810', '%Y%m%d')
        self.timestamp = Timestamp(service=self.script.name, timestamp=stamp)
        self._db.add(self.timestamp)

    def teardown(self):
        # delete any created records, files and directories
        test_timestamps = self._db.query(Timestamp).filter(
            Timestamp.service.like('Test Database Migration Script - %')
        )
        for timestamp in test_timestamps.all():
            self._db.delete(timestamp)

        for fd, fpath in self.migration_files:
            os.close(fd)
            os.remove(fpath)

        for directory in [self.core_migration_dir, self.parent_migration_dir]:
            if not os.listdir(directory):
                os.rmdir(directory)

        super(TestDatabaseMigrationScript, self).teardown()

    def test_directories_by_priority(self):
        core = os.path.split(os.path.split(__file__)[0])[0]
        parent = os.path.split(core)[0]
        expected_core = os.path.join(core, 'migration')
        expected_parent = os.path.join(parent, 'migration')

        eq_(
            [expected_core, expected_parent],
            self.script.directories_by_priority
        )

    def test_fetch_migration_files(self):
        result = self.script.fetch_migration_files()
        result_migrations, result_migrations_by_dir = result

        for mfd, migration_file in self.migration_files:
            assert os.path.split(migration_file)[1] in result_migrations

        def extract_filenames(core=True):
            pathnames = [pathname for desc, pathname in self.migration_files]
            if core:
                pathnames = [p for p in pathnames if 'core' in p]
            else:
                pathnames = [p for p in pathnames if 'core' not in p]

            return [os.path.split(p)[1] for p in pathnames]

        # Ensure that all the expected migrations from CORE are included in
        # the 'core' directory array in migrations_by_directory.
        core_migration_files = extract_filenames()

        # At some point, August 10th, 2026 will come and go and a migration
        # may be added and this unit test will start to fail right below.
        # At that point, move all dates to the 2030s.
        #
        # This is fine. <insert now-outdatedly-retro dog cartoon meme by KC Green>
        eq_(2, len(core_migration_files))
        for filename in core_migration_files:
            assert filename in result_migrations_by_dir[self.core_migration_dir]

        # Ensure that all the expected migrations from the parent server
        # are included in the appropriate array in migrations_by_directory.
        parent_migration_files = extract_filenames(core=False)
        eq_(2, len(parent_migration_files))
        for filename in parent_migration_files:
            assert filename in result_migrations_by_dir[self.parent_migration_dir]

    def test_migration_files(self):
        """Removes migration files that aren't python or SQL from a list."""

        migrations = [
            '.gitkeep', '20250521-make-bananas.sql', '20260810-do-a-thing.py',
            '20260802-did-a-thing.pyc', 'why-am-i-here.rb'
        ]

        result = self.script._migration_files(migrations)
        eq_(2, len(result))
        eq_(['20250521-make-bananas.sql', '20260810-do-a-thing.py'], result)

    def test_get_new_migrations(self):
        """Filters out migrations that were run on or before a given timestamp"""

        migrations = [
            '20271202-future-migration-funtime.sql',
            '20250521-make-bananas.sql',
            '20260810-last-timestamp',
            '20260811-do-a-thing.py',
            '20260809-already-done.sql'
        ]

        result = self.script.get_new_migrations(self.timestamp, migrations)
        # Expected migrations will be sorted by timestamp.
        expected = [
            '20260811-do-a-thing.py', '20271202-future-migration-funtime.sql'
        ]

        eq_(2, len(result))
        eq_(expected, result)

        # If the timestamp has a counter, the filter only finds new migrations
        # past the counter.
        migrations = [
            '20271202-future-migration-funtime.sql',
            '20260810-last-timestamp.sql',
            '20260810-1-do-a-thing.sql',
            '20260810-2-do-all-the-things.sql',
            '20260809-already-done.sql'
        ]
        self.timestamp.counter = 1
        result = self.script.get_new_migrations(self.timestamp, migrations)
        expected = [
            '20260810-2-do-all-the-things.sql',
            '20271202-future-migration-funtime.sql'
        ]

        eq_(2, len(result))
        eq_(expected, result)

        # If the timestamp has a (unlikely) mix of counter and non-counter
        # migrations with the same datetime, migrations with counters are
        # sorted after migrations without them.
        migrations = [
            '20260810-do-a-thing.sql',
            '20271202-1-more-future-migration-funtime.sql',
            '20260810-1-do-all-the-things.sql',
            '20260809-already-done.sql',
            '20271202-future-migration-funtime.sql',
        ]
        self.timestamp.counter = None

        result = self.script.get_new_migrations(self.timestamp, migrations)
        expected = [
            '20260810-1-do-all-the-things.sql',
            '20271202-future-migration-funtime.sql',
            '20271202-1-more-future-migration-funtime.sql'
        ]
        eq_(3, len(result))
        eq_(expected, result)

    def test_update_timestamp(self):
        """Resets a timestamp according to the date of a migration file"""

        migration = '20271202-future-migration-funtime.sql'

        assert self.timestamp.timestamp.strftime('%Y%m%d') != migration[0:8]
        self.script.update_timestamp(self.timestamp, migration)
        eq_(self.timestamp.timestamp.strftime('%Y%m%d'), migration[0:8])

        # It also takes care of counter digits when multiple migrations
        # exist for the same date.
        migration = '20260810-2-do-all-the-things.sql'
        self.script.update_timestamp(self.timestamp, migration)
        eq_(self.timestamp.timestamp.strftime('%Y%m%d'), migration[0:8])
        eq_(str(self.timestamp.counter), migration[9])

    def test_running_a_migration_updates_the_timestamp(self):
        future_time = datetime.datetime.strptime('20261030', '%Y%m%d')
        self.timestamp.timestamp = future_time

        # Create a test migration after that point and grab relevant info
        # about it.
        self._create_test_migration_file(
            self.core_migration_dir, 'SINGLE', 'sql',
            migration_date='20261202'
        )

        # Pop the last migration filepath off and run the migration with the
        # relevant information.
        migration_filepath = self.migration_files[-1][1]
        directory, migration_filename = os.path.split(migration_filepath)
        migrations_by_dir = {
            self.core_migration_dir : [migration_filename],
            self.parent_migration_dir : []
        }

        # Running the migration updates the timestamp
        self.script.run_migrations(
            [migration_filename], migrations_by_dir, self.timestamp
        )
        eq_(self.timestamp.timestamp.strftime('%Y%m%d'), '20261202')

        # Even when there are counters.
        self._create_test_migration_file(
            self.core_migration_dir, 'COUNTER', 'sql',
            migration_date='20261203-3'
        )
        migration_filename = os.path.split(self.migration_files[-1][1])[1]
        migrations_by_dir[self.core_migration_dir] = [migration_filename]
        self.script.run_migrations(
            [migration_filename], migrations_by_dir, self.timestamp
        )
        eq_(self.timestamp.timestamp.strftime('%Y%m%d'), '20261203')
        eq_(self.timestamp.counter, 3)

    def test_all_migration_files_are_run(self):
        self.script.do_run()

        # There are two test timestamps in the database, confirming that
        # the test SQL files created by self._create_test_migration_files()
        # have been run.
        timestamps = self._db.query(Timestamp).filter(
            Timestamp.service.like('Test Database Migration Script - %')
        ).order_by(Timestamp.service).all()
        eq_(2, len(timestamps))

        # A timestamp has been generated from each migration directory.
        eq_(True, timestamps[0].service.endswith('CORE'))
        eq_(True, timestamps[1].service.endswith('SERVER'))

        for timestamp in timestamps:
            self._db.delete(timestamp)

        # There are two temporary files created in core/tests,
        # confirming that the test Python files created by
        # self._create_test_migration_files() have been run.
        test_dir = os.path.split(__file__)[0]
        all_files = os.listdir(test_dir)
        test_generated_files = sorted([f for f in all_files
                                       if f.startswith(('CORE', 'SERVER'))])
        eq_(2, len(test_generated_files))

        # A file has been generated from each migration directory.
        assert 'CORE' in test_generated_files[0]
        assert 'SERVER' in test_generated_files[1]

        for filename in test_generated_files:
            os.remove(os.path.join(test_dir, filename))
