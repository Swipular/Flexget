import logging
from datetime import datetime

from sqlalchemy import (
    select,
    update,
    Index,
    Boolean,
    Column,
    Integer,
    Unicode,
    DateTime,
    ForeignKey,
    or_,
)
from sqlalchemy.orm import relation

from flexget import db_schema
from flexget import plugin
from flexget.event import event
from flexget.utils.database import with_session
from flexget.utils.sqlalchemy_utils import table_schema, table_add_column

try:
    # NOTE: Importing other plugins is discouraged!
    from flexget.components.imdb.utils import extract_id
except ImportError:
    raise plugin.DependencyError(issued_by=__name__, missing='imdb')


log = logging.getLogger('seen.db')
Base = db_schema.versioned_base('seen', 4)


@db_schema.upgrade('seen')
def upgrade(ver, session):
    if ver is None:
        log.info('Converting seen imdb_url to imdb_id for seen movies.')
        field_table = table_schema('seen_field', session)
        for row in session.execute(
            select([field_table.c.id, field_table.c.value], field_table.c.field == 'imdb_url')
        ):
            new_values = {'field': 'imdb_id', 'value': extract_id(row['value'])}
            session.execute(update(field_table, field_table.c.id == row['id'], new_values))
        ver = 1
    if ver == 1:
        field_table = table_schema('seen_field', session)
        log.info('Adding index to seen_field table.')
        Index('ix_seen_field_seen_entry_id', field_table.c.seen_entry_id).create(bind=session.bind)
        ver = 2
    if ver == 2:
        log.info('Adding local column to seen_entry table')
        table_add_column('seen_entry', 'local', Boolean, session, default=False)
        ver = 3
    if ver == 3:
        # setting the default to False in the last migration was broken, fix the data
        log.info('Repairing seen table')
        entry_table = table_schema('seen_entry', session)
        session.execute(update(entry_table, entry_table.c.local == None, {'local': False}))
        ver = 4

    return ver


class SeenEntry(Base):
    __tablename__ = 'seen_entry'

    id = Column(Integer, primary_key=True)
    title = Column(Unicode)
    reason = Column(Unicode)
    task = Column('feed', Unicode)
    added = Column(DateTime)
    local = Column(Boolean)

    fields = relation('SeenField', backref='seen_entry', cascade='all, delete, delete-orphan')

    def __init__(self, title, task, reason=None, local=None):
        if local is None:
            local = False
        self.title = title
        self.reason = reason
        self.task = task
        self.added = datetime.now()
        self.local = local

    def __str__(self):
        return '<SeenEntry(title=%s,reason=%s,task=%s,added=%s)>' % (
            self.title,
            self.reason,
            self.task,
            self.added,
        )

    def to_dict(self):
        fields = []
        for field in self.fields:
            fields.append(field.to_dict())

        seen_entry_object = {
            'id': self.id,
            'title': self.title,
            'reason': self.reason,
            'task': self.task,
            'added': self.added,
            'local': self.local,
            'fields': fields,
        }
        return seen_entry_object


class SeenField(Base):
    __tablename__ = 'seen_field'

    id = Column(Integer, primary_key=True)
    seen_entry_id = Column(Integer, ForeignKey('seen_entry.id'), nullable=False, index=True)
    field = Column(Unicode)
    value = Column(Unicode, index=True)
    added = Column(DateTime)

    def __init__(self, field, value):
        self.field = field
        self.value = value
        self.added = datetime.now()

    def __str__(self):
        return '<SeenField(field=%s,value=%s,added=%s)>' % (self.field, self.value, self.added)

    def to_dict(self):
        return {
            'field_name': self.field,
            'field_id': self.id,
            'value': self.value,
            'added': self.added,
            'seen_entry_id': self.seen_entry_id,
        }


@with_session
def add(title, task_name, fields, reason=None, local=None, session=None):
    """
    Adds seen entries to DB

    :param title: name of title to be added
    :param task_name: name of task to be added
    :param fields: Dict of fields to be added to seen object
    :return: Seen Entry object as committed to DB
    """
    se = SeenEntry(title, task_name, reason, local)
    for field, value in list(fields.items()):
        sf = SeenField(field, value)
        se.fields.append(sf)
    session.add(se)
    session.commit()
    return se.to_dict()


@with_session
def search_by_field_values(field_value_list, task_name, local=False, session=None):
    """
    Return a SeenEntry instance if it matches field values
    :param field_value_list: List of field values to match
    :param task_name: Name of task to compare to in case local flag is sent
    :param local: Local flag
    :param session: Current session
    :return: SeenEntry Object or None
    """
    found = session.query(SeenField).join(SeenEntry).filter(SeenField.value.in_(field_value_list))
    if local:
        found = found.filter(SeenEntry.task == task_name)
    else:
        # Entries added from CLI were having local marked as None rather than False for a while gh#879
        found = found.filter(or_(SeenEntry.local == False, SeenEntry.local == None))
    return found.first()


@event('manager.db_cleanup')
def db_cleanup(manager, session):
    # TODO: Look into this, is it still valid?
    log.debug('TODO: Disabled because of ticket #1321')
    return

    # Remove seen fields over a year old
    # result = session.query(SeenField).filter(SeenField.added < datetime.now() - timedelta(days=365)).delete()
    # if result:
    #    log.verbose('Removed %d seen fields older than 1 year.' % result)


@with_session
def search(
    count=None,
    value=None,
    status=None,
    start=None,
    stop=None,
    order_by='added',
    descending=False,
    session=None,
):
    query = session.query(SeenEntry).join(SeenField)
    if value:
        query = query.filter(SeenField.value.like(value))
    if status is not None:
        query = query.filter(SeenEntry.local == status)
    if count:
        return query.group_by(SeenEntry).count()
    if descending:
        query = query.order_by(getattr(SeenEntry, order_by).desc())
    else:
        query = query.order_by(getattr(SeenEntry, order_by))
    return query.group_by(SeenEntry).slice(start, stop).from_self()


@with_session
def get_entry_by_id(entry_id, session=None):
    return session.query(SeenEntry).filter(SeenEntry.id == entry_id).one()


@with_session
def forget_by_id(entry_id, session=None):
    """
    Delete SeenEntry via its ID
    :param entry_id: SeenEntry ID
    :param session: DB Session
    """
    entry = get_entry_by_id(entry_id, session=session)
    log.debug('Deleting seen entry with ID {0}'.format(entry_id))
    session.delete(entry)
