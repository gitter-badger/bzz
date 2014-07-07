#!/usr/bin/python
# -*- coding: utf-8 -*-

# This file is part of bzz.
# https://github.com/heynemann/bzz

# Licensed under the MIT license:
# http://www.opensource.org/licenses/MIT-license
# Copyright (c) 2014 Bernardo Heynemann heynemann@gmail.com

import tornado.web
import tornado.gen as gen
from six.moves.urllib.parse import unquote

try:
    import ujson as json
except ImportError:
    import json

import bzz.signals as signals


class ModelRestHandler(tornado.web.RequestHandler):
    def initialize(self, model, name, prefix):
        self.model = model
        self.name = name
        self.prefix = prefix

    def write_json(self, obj):
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(obj))

    @gen.coroutine
    def get(self, *args, **kwargs):
        obj, field_name, model, pk = yield self.get_parent_model(args)

        if pk is None:
            yield self.list()
            return

        if obj is None:
            self.send_error(status_code=404)
            return

        self.write_json(self.dump_object(obj))
        self.finish()

    @gen.coroutine
    def post(self, *args, **kwargs):
        obj, field_name, model, pk = yield self.get_parent_model(args)
        instance = yield self.save_new_instance(model, self.get_request_data())
        yield self.associate_instance(obj, field_name, instance)
        signals.post_create_instance.send(model, instance=instance, handler=self)
        pk = yield self.get_instance_id(instance)
        self.set_header('X-Created-Id', pk)
        self.set_header('location', '/%s%s/%s/' % (
            self.prefix,
            self.name,
            pk
        ))
        self.write('OK')

    @gen.coroutine
    def get_parent_model(self, args):
        obj = None
        args = [arg for arg in args if arg]
        model = None
        id_ = None

        for part in args[:-1]:
            property_, property_id = part.split('/')

            if obj is None:
                obj = yield self.get_instance(property_id)
            else:
                obj = getattr(obj, property_)

        field_name = args[-1].lstrip('/')
        if '/' in field_name:
            field_name, id_ = field_name.split('/')
            if obj is None:
                obj = yield self.get_instance(id_)
                model = obj.__class__
            else:
                obj = getattr(obj, field_name)

        if model is None:
            model = yield self.get_model(obj, field_name)

        raise gen.Return([obj, field_name, model, id_])

    @gen.coroutine
    def put(self, *args, **kwargs):
        obj, field_name, model, pk = yield self.get_parent_model(args)
        instance, updated = yield self.update_instance(pk, self.get_request_data())
        signals.post_update_instance.send(self.model, instance=instance, updated_fields=updated, handler=self)
        self.write('OK')

    @gen.coroutine
    def delete(self, *args, **kwargs):
        obj, field_name, model, pk = yield self.get_parent_model(args)
        instance = yield self.delete_instance(pk)
        if instance:
            signals.post_delete_instance.send(self.model, instance=instance, handler=self)
            self.write('OK')
        else:
            self.write('FAIL')

    @gen.coroutine
    def list(self):
        items = yield self.get_list()
        dump = []
        for item in items:
            dump.append(self.dump_object(item))

        self.write_json(dump)

    def get_request_data(self):
        data = {}

        if self.request.body:
            items = self.request.body.decode('utf-8').split('&')
            for item in items:
                key, value = item.split('=')
                data[key] = unquote(value)
        else:
            for arg in list(self.request.arguments.keys()):
                data[arg] = self.get_argument(arg)
                if data[arg] == '':  # Tornado 3.0+ compatibility... Hard to test...
                    data[arg] = None

        return data

    def dump_object(self, instance):
        return json.dumps(instance)