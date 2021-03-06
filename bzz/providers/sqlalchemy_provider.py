#!/usr/bin/python
# -*- coding: utf-8 -*-

# This file is part of bzz.
# https://github.com/heynemann/bzz

# Licensed under the MIT license:
# http://www.opensource.org/licenses/MIT-license
# Copyright (c) 2014 Bernardo Heynemann heynemann@gmail.com

import math

import tornado.gen as gen
from sqlalchemy.orm.relationships import RelationshipProperty
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.inspection import inspect
from sqlalchemy.ext.declarative import declarative_base

import bzz.model as bzz


Base = declarative_base()


class MaxDepthError(RuntimeError):
    pass


class SQLAlchemyProvider(bzz.ModelProvider):
    @property
    def db(self):
        return self.application.db

    @classmethod
    def get_model_name(cls, model):
        return model.__name__

    @classmethod
    def get_model_collection(cls, model):
        return model.__table__

    @classmethod
    def get_model_fields(cls, model):
        mapper = inspect(model)
        columns = mapper.columns
        relations = mapper.relationships
        columns.update(relations)
        return columns

    @classmethod
    def get_model(cls, field):
        if not isinstance(field, RelationshipProperty):
            return None

        for c in Base._decl_class_registry.values():
            if hasattr(c, '__tablename__') and c.__tablename__ == field.table.fullname:
                return c

        return None

    @classmethod
    def get_field_target_name(cls, field):
        if isinstance(field, RelationshipProperty):
            return field.key
        return field.name

    @classmethod
    def get_document_type(cls, field):
        if cls.is_list_field(field):
            return field.argument
        return field.__class__

    @classmethod
    def allows_create_on_associate(cls, field):
        return False

    @classmethod
    def is_lazy_loaded(cls, field):
        if not isinstance(field, RelationshipProperty):
            return False

        return field.lazy

    @classmethod
    def is_list_field(cls, field):
        if isinstance(field, InstrumentedAttribute):
            field = field.parent.relationships[field.key]

        return isinstance(field, RelationshipProperty) and field.uselist

    @classmethod
    def is_reference_field(cls, field):
        return isinstance(field, RelationshipProperty)

    @classmethod
    def is_embedded_field(cls, field):
        return False

    @gen.coroutine
    def save_new_instance(self, model, data):
        instance = model()
        fields = self.get_model_fields(model)

        for key, value in data.items():
            if '.' in key or '[]' in key:
                yield self.fill_property(model, instance, key, value)
            else:
                field = fields.get(key)
                if self.is_reference_field(field):
                    value = yield self.get_instance(
                        value,
                        self.get_model(field)
                    )
                setattr(instance, key, value)

        self.db.add(instance)
        self.db.flush()
        self.db.commit()

        raise gen.Return((instance, None))

    @gen.coroutine
    def fill_property(self, model, instance, key, value, updated_fields=None):
        parts = key.split('.')
        field_name = parts[0]
        multiple = False
        if field_name.endswith('[]'):
            multiple = True
            field_name = field_name.replace('[]', '')

        property_name = '.'.join(parts[1:])

        if '.' not in property_name:
            if updated_fields is not None:
                updated_fields[field_name] = {
                    'from': getattr(instance, field_name),
                    'to': str(value)
                }

            field = getattr(model, field_name, None)
            if isinstance(field, InstrumentedAttribute):
                field = field.parent.relationships[field.key]

            child_model = self.get_model(field)
            if multiple and self.is_list_field(field):
                if not isinstance(value, (tuple, list)):
                    value = [value]

                list_property = getattr(instance, field_name)
                for item in value:
                    child_instance = yield self.get_instance(
                        item, model=child_model
                    )

                    list_property.append(child_instance)
            else:
                setattr(getattr(instance, field_name), property_name, value)
        else:
            new_instance = getattr(instance, field_name)
            yield self.fill_property(
                new_instance.__class__, new_instance,
                property_name, value
            )

    @gen.coroutine
    def update_instance(
            self, pk, data, model=None, instance=None, parent=None):
        if model is None:
            model = self.model

        fields = self.get_model_fields(model)

        if instance is None:
            instance = yield self.get_instance(pk, model)

        updated_fields = {}
        for field_name, value in self.get_request_data().items():
            if '.' in field_name:
                yield self.fill_property(
                    model, instance, field_name, value, updated_fields
                )
            else:
                field = fields.get(field_name)
                if self.is_reference_field(field):
                    value = yield self.get_instance(
                        value, self.get_model(field)
                    )
                updated_fields[field_name] = {
                    'from': getattr(instance, field_name),
                    'to': value
                }
                setattr(instance, field_name, value)

        yield self.save_instance(instance)

        raise gen.Return((None, instance, updated_fields))

    @gen.coroutine
    def save_instance(self, instance):
        self.db.flush()
        self.db.commit()

        raise gen.Return((instance, None))

    @gen.coroutine
    def delete_instance(self, pk):
        instance = yield self.get_instance(pk)
        if instance is not None:
            self.db.delete(instance)
            yield self.save_instance(instance)
        raise gen.Return(instance)

    @gen.coroutine
    def get_instance(self, instance_id, model=None):
        if model is None:
            model = self.model

        queryset = self.db.query(model)
        if hasattr(model, 'get_instance_queryset'):
            queryset = model.get_instance_queryset(model, queryset, instance_id, self)

        instance = None
        field = self.get_id_field_name(model)

        instance = queryset.filter(field == instance_id).first()

        raise gen.Return(instance)

    @gen.coroutine
    def get_list(self, items=None, page=1, per_page=20, filters=None):
        if filters is None:
            filters = {}

        queryset = self.db.query(self.model)
        if hasattr(self.model, 'get_list_queryset'):
            queryset = self.model.get_list_queryset(queryset, self)

        if filters:
            for filter_, value in filters.items():
                queryset = queryset.filter(
                    self.get_field(self.model, filter_) == value
                )

        count = queryset.count()

        pages = int(math.ceil(count / float(per_page)))
        if pages == 0:
            raise gen.Return([])

        if page > pages:
            page = pages

        page -= 1

        start = per_page * page
        stop = start + per_page

        items = queryset[start:stop]
        raise gen.Return(items)

    def dump_list(self, items):
        dumped = []

        for item in items:
            dumped.append(self.dump_instance(item))

        return dumped

    def dump_instance(self, instance, depth=0):
        if depth > 1:
            raise MaxDepthError()

        if instance is None:
            return {}

        method = getattr(instance, 'to_dict', None)

        if method:
            return method()

        result = dict()
        for node_name, node in self.tree.find_by_class(instance.__class__).children.items():
            if node.model_type is not None:
                if not node.is_multiple:
                    try:
                        result[node_name] = self.dump_instance(getattr(instance, node_name), depth=depth + 1)
                    except MaxDepthError:
                        pass
            else:
                result[node_name] = getattr(instance, node_name)

        return result

    @gen.coroutine
    def get_instance_id(self, instance):
        field = getattr(instance.__class__, 'get_id_field_name', None)
        if field:
            raise gen.Return(str(getattr(instance, field().name)))

        raise gen.Return(str(instance.id))

    def get_id_field_name(self, model=None):
        if model is None:
            model = self.model

        fields = self.get_model_fields(model)
        field = getattr(model, 'get_id_field_name', None)
        if field:
            name = field().name
        else:
            name = 'id'

        id_field = fields.get(name, None)

        if id_field is None:
            raise ValueError("Could not find a 'get_id_field_name' method on '%s', neither an 'id' field could be found in same model." % model.__name__)

        return id_field

    @gen.coroutine
    def associate_instance(self, obj, field_name, instance):
        if obj is None:
            return

        field = getattr(obj.__class__, field_name)
        if isinstance(field, InstrumentedAttribute):
            field = field.parent.relationships[field.key]

        if self.is_list_field(field):
            getattr(obj, field_name).append(instance)
        else:
            setattr(obj, field_name, instance)

        yield self.save_instance(obj)

        raise gen.Return((obj, None))

    def get_property_model(self, obj, field_name):
        property_name = field_name
        pk = None

        if '/' in field_name:
            property_name, pk = field_name.split('/')

        fields = self.get_model_fields(obj.__class__)
        field = fields[property_name]
        return self.get_document_type(field)

    @gen.coroutine
    def is_multiple(self, path):
        parts = [part.lstrip('/').split('/') for part in path if part]
        to_return = False
        model = self.model

        if len(parts) == 1 and len(parts[0]) == 1:
            raise gen.Return(True)

        for part in parts[1:]:
            to_return = False
            path = part[0]
            field = getattr(model, path)
            to_return = self.is_list_field(field)
            model = self.get_model(field)

        raise gen.Return(to_return)

    @gen.coroutine
    def is_reference(self, path):
        parts = [part.lstrip('/').split('/') for part in path if part]
        to_return = False
        model = self.model

        for part in parts[1:]:
            to_return = False
            model_path = part[0]
            field = getattr(model, model_path)

            if isinstance(field, InstrumentedAttribute):
                field = field.parent.relationships[field.key]

            to_return = self.is_reference_field(field)
            model = self.get_model(field)

        raise gen.Return(to_return)

    @gen.coroutine
    def get_model_from_path(self, path):
        parts = [part.lstrip('/').split('/') for part in path if part]
        model = self.model

        if len(parts) == 1 and len(parts[0]) == 1:
            raise gen.Return(model)

        for part in parts[1:]:
            path = part[0]
            field = getattr(model, path)
            model = self.get_model(field)

        raise gen.Return(model)
