# -*- coding: utf-8 -*-
#
#  Copyright 2016, 2017 Ramil Nugmanov <stsouko@live.ru>
#  This file is part of MWUI.
#
#  MWUI is free software; you can redistribute it and/or modify
#  it under the terms of the GNU Affero General Public License as published by
#  the Free Software Foundation; either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU Affero General Public License for more details.
#
#  You should have received a copy of the GNU Affero General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.
#
import uuid
from collections import defaultdict
from os import path
from .logins import UserLogin
from .config import (UPLOAD_PATH, StructureStatus, TaskStatus, ModelType, TaskType, REDIS_HOST, REDIS_JOB_TIMEOUT,
                     REDIS_PASSWORD, REDIS_PORT, REDIS_TTL, StructureType, UserRole, BLOG_POSTS_PER_PAGE, AdditiveType)
from .models import Task, Structure, Additive, Model, Additiveset, Destination, User, Result
from .redis import RedisCombiner
from flask import Blueprint, url_for, send_from_directory, request, Response
from flask_login import current_user, login_user
from flask_restful import reqparse, fields, marshal, abort, inputs, Api, Resource
from functools import wraps
from pony.orm import db_session, select, left_join
from validators import url
from werkzeug import datastructures
from typing import Dict, Tuple
from flask_restful_swagger import swagger

api_bp = Blueprint('api', __name__)
api = swagger.docs(Api(api_bp), apiVersion='1.0', description='MWUI API', api_spec_url='/doc/spec')

redis = RedisCombiner(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, result_ttl=REDIS_TTL,
                      job_timeout=REDIS_JOB_TIMEOUT)
task_types_desc = ', '.join('{0.value} - {0.name}'.format(x) for x in TaskType)


class ModelTypeField(fields.Raw):
    def format(self, value):
        return ModelType(value)


@swagger.model
class LogInFields:
    resource_fields = dict(user=fields.String, password=fields.String)


@swagger.model
class TaskPostResponseFields:
    resource_fields = dict(task=fields.String, status=fields.Integer, type=fields.Integer,
                           date=fields.String, user=fields.Integer)


@swagger.model
class DestinationsFields:
    resource_fields = dict(host=fields.String, port=fields.Integer(6379), password=fields.String, name=fields.String)


@swagger.model
@swagger.nested(destinations=DestinationsFields.__name__)
class ModelRegisterFields:
    resource_fields = dict(example=fields.String, description=fields.String, type=ModelTypeField, name=fields.String,
                           destinations=fields.List(fields.Nested(DestinationsFields.resource_fields)))


@swagger.model
class AdditivesFields:
    resource_fields = dict(additive=fields.Integer, amount=fields.Float)


@swagger.model
class ModelsFields:
    resource_fields = dict(model=fields.Integer, name=fields.String)


@swagger.model
@swagger.nested(additives=AdditivesFields.__name__, models=ModelsFields.__name__)
class TaskStructureFields:
    resource_fields = dict(structure=fields.Integer, data=fields.String, temperature=fields.Float(298),
                           pressure=fields.Float(1), todelete=fields.Boolean(False),
                           additives=fields.List(fields.Nested(AdditivesFields.resource_fields)),
                           models=fields.List(fields.Nested(ModelsFields.resource_fields)))


@api_bp.route('/task/batch_file/<string:file>', methods=['GET'])
def batch_file(file):
    return send_from_directory(directory=UPLOAD_PATH, filename=file)


def get_model(_type):
    with db_session:
        return next(dict(model=m.id, name=m.name, description=m.description, type=m.type,
                         destinations=[dict(host=x.host, port=x.port, password=x.password, name=x.name)
                                       for x in m.destinations])
                    for m in select(m for m in Model if m.model_type == _type.value))


def get_additives():
    with db_session:
        return {a.id: dict(additive=a.id, name=a.name, structure=a.structure, type=a.type)
                for a in select(a for a in Additive)}


def get_models_list(skip_prep=True, skip_dest_and_example=False):
    with db_session:
        res = {}
        for m in (select(m for m in Model if m.model_type in (ModelType.MOLECULE_MODELING.value,
                                                              ModelType.REACTION_MODELING.value))
                  if skip_prep else select(m for m in Model)):
            res[m.id] = dict(model=m.id, name=m.name, description=m.description, type=m.type)
            if not skip_dest_and_example:
                res[m.id]['example'] = m.example
                res[m.id]['destinations'] = [dict(host=x.host, port=x.port, password=x.password, name=x.name)
                                             for x in m.destinations]
        return res


def fetchtask(task, status):
    job = redis.fetch_job(task)
    if job is None:
        abort(404, message='Invalid task id. Perhaps this task has already been removed')

    if not job:
        abort(500, message='modeling server error')

    if not job['is_finished']:
        abort(512, message='PROCESSING.Task not ready')

    if job['result']['status'] != status:
        abort(406, message='Task status is invalid. Task status is [%s]' % job['result']['status'].name)

    if job['result']['user'] != current_user.id:
        abort(403, message='User access deny. You do not have permission to this task')

    return job['result'], job['ended_at']


def format_results(task, status, page=None):
    result, ended_at = fetchtask(task, status)
    out = dict(task=task, date=ended_at.strftime("%Y-%m-%d %H:%M:%S"), status=result['status'].value,
               type=result['type'].value, user=result['user'], structures=[])

    for s in result['structures'][(page - 1) * BLOG_POSTS_PER_PAGE: page * BLOG_POSTS_PER_PAGE] \
            if page else result['structures']:
        out['structures'].append(dict(status=s['status'].value, type=s['type'].value, structure=s['structure'],
                                      data=s['data'], pressure=s['pressure'], temperature=s['temperature'],
                                      additives=[dict(additive=a['additive'], name=a['name'], structure=a['structure'],
                                                      type=a['type'].value) for a in s['additives']],
                                      models=[dict(type=m['type'].value, model=m['model'], name=m['name'],
                                                   results=[dict(type=r['type'].value, key=r['key'], value=r['value'])
                                                            for r in m['results']]) for m in s['models']]))
    return out


def authenticate(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if current_user.is_authenticated:
            return func(*args, **kwargs)

        abort(401, message=dict(user='not authenticated'))

    return wrapper


def auth_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if auth:
            u = UserLogin.get(auth.username.lower(), auth.password)
            if u and u.role_is(UserRole.ADMIN):
                return f(*args, **kwargs)

        return Response('Could not verify your access level for that URL.\n'
                        'You have to login with proper credentials', 401,
                        {'WWW-Authenticate': 'Basic realm="Login Required"'})

    return decorated


class AuthResource(Resource):
    method_decorators = [authenticate]


class AdminResource(Resource):
    method_decorators = [auth_admin]


class RegisterModels(AdminResource):
    def post(self):
        data = marshal(request.get_json(force=True), ModelRegisterFields.resource_fields)
        models = data if isinstance(data, list) else [data]
        available = {x['name']: [(d['host'], d['port'], d['name']) for d in x['destinations']]
                     for x in get_models_list(skip_prep=False).values()}
        report = []
        for m in models:
            if m['destinations']:
                if m['name'] not in available:
                    with db_session:
                        new_m = Model(type=m['type'], name=m['name'], description=m['description'],
                                      example=m['example'])

                        for d in m['destinations']:
                            Destination(model=new_m, **d)

                    report.append(dict(model=new_m.id, name=new_m.name, description=new_m.description,
                                       type=new_m.type.value,
                                       example=new_m.example,
                                       destinations=[dict(host=x.host, port=x.port, name=x.name)
                                                     for x in new_m.destinations]))
                else:
                    tmp = []
                    with db_session:
                        model = Model.get(name=m['name'])
                        for d in m['destinations']:
                            if (d['host'], d['port'], d['name']) not in available[m['name']]:
                                tmp.append(Destination(model=model, **d))

                    if tmp:
                        report.append(dict(model=model.id, name=model.name, description=model.description,
                                           type=model.type.value, example=model.example,
                                           destinations=[dict(host=x.host, port=x.port, name=x.name)
                                                         for x in tmp]))
        return report, 201


class AvailableModels(Resource):
    def get(self):
        out = []
        for x in get_models_list().values():
            x.pop('destinations')
            x['type'] = x['type'].value
            out.append(x)
        return out, 200


class AvailableAdditives(Resource):
    def get(self):
        out = []
        for x in get_additives().values():
            x['type'] = x['type'].value
            out.append(x)
        return out, 200


results_fetch = reqparse.RequestParser()
results_fetch.add_argument('page', type=inputs.positive)


class ResultsTask(AuthResource):
    """ ===================================================
        collector of modeled tasks (individually). return json
        ===================================================
    """
    def get(self, task):
        try:
            task = int(task)
        except ValueError:
            abort(404, message='invalid task id. Use int Luke')

        page = results_fetch.parse_args().get('page')
        with db_session:
            result = Task.get(id=task)
            if not result:
                abort(404, message='Invalid task id. Perhaps this task has already been removed')

            if result.user.id != current_user.id:
                abort(403, message='User access deny. You do not have permission to this task')

            models = get_models_list(skip_dest_and_example=True)
            for v in models.values():
                v['type'] = v['type'].value

            additives = get_additives()

            s = select(s for s in Structure if s.task == result).order_by(Structure.id)
            if page:
                s = s.page(page, pagesize=BLOG_POSTS_PER_PAGE)

            structures = {x.id: dict(structure=x.id, data=x.structure, temperature=x.temperature, pressure=x.pressure,
                                     type=x.structure_type, status=x.structure_status, additives=[], models=[])
                          for x in s}

            r = left_join((s.id, r.model.id, r.key, r.value, r.result_type)
                          for s in Structure for r in s.results if s.id in structures.keys() and r is not None)

            a = left_join((s.id, a.additive.id, a.amount)
                          for s in Structure for a in s.additives if s.id in structures.keys() and a is not None)

            for s, a, aa in a:
                tmp = dict(amount=aa)
                tmp.update(additives[a])
                structures[s]['additives'].append(tmp)

            tmp_models = defaultdict(dict)
            for s, m, rk, rv, rt in r:
                tmp_models[s].setdefault(m, []).append(dict(key=rk, value=rv, type=rt))

            for s, mr in tmp_models.items():
                for m, r in mr.items():
                    tmp = dict(results=r)
                    tmp.update(models[m])
                    structures[s]['models'].append(tmp)

        return dict(task=task, status=TaskStatus.DONE.value, date=result.date.strftime("%Y-%m-%d %H:%M:%S"),
                    type=result.task_type, user=result.user.id, structures=list(structures.values())), 200

    def post(self, task):
        result, ended_at = fetchtask(task, TaskStatus.DONE)

        with db_session:
            _task = Task(type=result['type'], date=ended_at, user=User[current_user.id])
            for s in result['structures']:
                _structure = Structure(structure=s['data'], type=s['type'], temperature=s['temperature'],
                                       pressure=s['pressure'], status=s['status'], task=_task)
                for a in s['additives']:
                    Additiveset(additive=Additive[a['additive']], structure=_structure, amount=a['amount'])

                for m in s['models']:
                    for r in m.get('results', []):
                        Result(model=m['model'], structure=_structure, type=r['type'], key=r['key'], value=r['value'])

        return dict(task=_task.id, status=TaskStatus.DONE.value, date=ended_at.strftime("%Y-%m-%d %H:%M:%S"),
                    type=result['type'].value, user=current_user.id), 201


class ModelTask(AuthResource):
    """ ===================================================
        api for task modeling.
        ===================================================
    """
    def get(self, task):
        page = results_fetch.parse_args().get('page')
        return format_results(task, TaskStatus.DONE, page=page), 200

    def post(self, task):
        data = marshal(request.get_json(force=True), TaskStructureFields.resource_fields)
        result = fetchtask(task, TaskStatus.PREPARED)[0]

        prepared = {s['structure']: s for s in result['structures']}
        structures = data if isinstance(data, list) else [data]
        tmp = {x['structure']: x for x in structures if x['structure'] in prepared}

        if 0 in tmp:
            abort(400, message='invalid structure data')

        additives = get_additives()
        models = get_models_list()

        for s, d in tmp.items():
            if d['todelete']:
                prepared.pop(s)
            else:
                if d['additives'] is not None:
                    alist = []
                    for a in d['additives']:
                        if a['additive'] in additives and (0 < a['amount'] <= 1
                                                           if additives[a['additive']]['type'] == AdditiveType.SOLVENT
                                                           else a['amount'] > 0):
                            a.update(additives[a['additive']])
                            alist.append(a)
                    prepared[s]['additives'] = alist

                if result['type'] != TaskType.MODELING:  # for search tasks assign compatible models
                    prepared[s]['models'] = [get_model(ModelType.select(prepared[s]['type'], result['type']))]

                elif d['models'] is not None and prepared[s]['status'] == StructureStatus.CLEAR:
                    prepared[s]['models'] = [models[m['model']] for m in d['models']
                                             if m['model'] in models and
                                             models[m['model']]['type'].compatible(prepared[s]['type'],
                                                                                   TaskType.MODELING)]

                if d['temperature']:
                    prepared[s]['temperature'] = d['temperature']

                if d['pressure']:
                    prepared[s]['pressure'] = d['pressure']

        result['structures'] = list(prepared.values())
        result['status'] = TaskStatus.MODELING

        new_job = redis.new_job(result)
        if new_job is None:
            abort(500, message='modeling server error')

        return dict(task=new_job['id'], status=result['status'].value, type=result['type'].value,
                    date=new_job['created_at'].strftime("%Y-%m-%d %H:%M:%S"), user=result['user']), 201


class PrepareTask(AuthResource):
    def get(self, task):
        page = results_fetch.parse_args().get('page')
        return format_results(task, TaskStatus.PREPARED, page=page), 200

    def post(self, task):
        data = marshal(request.get_json(force=True), TaskStructureFields.resource_fields)
        result = fetchtask(task, TaskStatus.PREPARED)[0]
        preparer = get_model(ModelType.PREPARER)

        prepared = {s['structure']: s for s in result['structures']}
        structures = data if isinstance(data, list) else [data]
        tmp = {x['structure']: x for x in structures if x['structure'] in prepared}

        if 0 in tmp:
            abort(400, message='invalid structure data')

        additives = get_additives()

        for s, d in tmp.items():
            if d['todelete']:
                prepared.pop(s)
            else:
                if d['additives'] is not None:
                    alist = []
                    for a in d['additives']:
                        if a['additive'] in additives and (0 < a['amount'] <= 1
                                                           if additives[a['additive']]['type'] == AdditiveType.SOLVENT
                                                           else a['amount'] > 0):
                            a.update(additives[a['additive']])
                            alist.append(a)
                    prepared[s]['additives'] = alist

                if d['data']:
                    prepared[s]['data'] = d['data']
                    prepared[s]['status'] = StructureStatus.RAW
                    prepared[s]['models'] = [preparer]
                elif s['status'] == StructureStatus.RAW:
                    prepared[s]['models'] = [preparer]

                if d['temperature']:
                    prepared[s]['temperature'] = d['temperature']

                if d['pressure']:
                    prepared[s]['pressure'] = d['pressure']

        result['structures'] = list(prepared.values())
        result['status'] = TaskStatus.PREPARING

        new_job = redis.new_job(result)
        if new_job is None:
            abort(500, message='modeling server error')

        return dict(task=new_job['id'], status=result['status'].value, type=result['type'].value,
                    date=new_job['created_at'].strftime("%Y-%m-%d %H:%M:%S"), user=result['user']), 201


class CreateTask(AuthResource):
    @swagger.operation(
        notes='Create new task',
        nickname='create',
        responseClass=TaskPostResponseFields.__name__,
        parameters=[dict(name='_type', description='Task type ID: %s' % task_types_desc, required=True,
                         allowMultiple=False, dataType='int', paramType='path'),
                    dict(name='structures', description='Structure of molecule or reaction with optional conditions',
                         required=True, allowMultiple=False, dataType=TaskStructureFields.__name__, paramType='body')],
        responseMessages=[dict(code=201, message="task created"),
                          dict(code=400, message="invalid structure data"),
                          dict(code=403, message="invalid task type"),
                          dict(code=500, message="modeling server error")])
    def post(self, _type):
        """
        Create new task

        also possible to send list of TaskStructureFields.

        data field is required. field should be a string containing marvin document or cml or smiles/smirks
        todelete and models fields not usable
        additive should be in list of available additives.
        amount should be in range 0 to 1 for additives type 0 [SOLVENT], and positive for overs.
        temperature in Kelvin
        pressure in Bar
        """
        try:
            _type = TaskType(_type)
        except ValueError:
            abort(403, message='invalid task type [%s]. valid values are %s' % (_type, task_types_desc))

        data = marshal(request.get_json(force=True), TaskStructureFields.resource_fields)

        additives = get_additives()

        preparer = get_model(ModelType.PREPARER)
        structures = data if isinstance(data, list) else [data]

        data = []
        for s, d in enumerate(structures, start=1):
            if d['data']:
                alist = []
                for a in d['additives'] or []:
                    if a['additive'] in additives and (0 < a['amount'] <= 1
                                                       if additives[a['additive']]['type'] == AdditiveType.SOLVENT
                                                       else a['amount'] > 0):
                        a.update(additives[a['additive']])
                        alist.append(a)

                data.append(dict(structure=s, data=d['data'], status=StructureStatus.RAW, type=StructureType.UNDEFINED,
                                 pressure=d['pressure'], temperature=d['temperature'],
                                 additives=alist, models=[preparer]))

        if not data:
            abort(400, message='invalid structure data')

        new_job = redis.new_job(dict(status=TaskStatus.NEW, type=_type, user=current_user.id, structures=data))

        if new_job is None:
            abort(500, message='modeling server error')

        return dict(task=new_job['id'], status=TaskStatus.PREPARING.value, type=_type.value,
                    date=new_job['created_at'].strftime("%Y-%m-%d %H:%M:%S"), user=current_user.id), 201


uf_post = reqparse.RequestParser()
uf_post.add_argument('file.url', type=str)
uf_post.add_argument('file.path', type=str)
uf_post.add_argument('structures', type=datastructures.FileStorage, location='files')


class UploadTask(AuthResource):
    @swagger.operation(
        notes='Structures file upload',
        nickname='upload',
        responseClass=TaskPostResponseFields.__name__,
        parameters=[dict(name='_type', description='Task type ID: %s' % task_types_desc, required=True,
                         allowMultiple=False, dataType='int', paramType='path'),
                    dict(name='structures', description='RDF SDF MRV SMILES file', required=True,
                         allowMultiple=False, dataType='file', paramType='body')],
        responseMessages=[dict(code=201, message="task created"),
                          dict(code=400, message="structure file required"),
                          dict(code=403, message="invalid task type"),
                          dict(code=500, message="modeling server error")])
    def post(self, _type: int) -> Tuple[Dict, int]:
        """
        Structures file upload

        Need for batch mode.
        Any chemical structure formats convertable with Chemaxon JChem can be passed.

        conditions in files should be present in next key-value format:
        additive.amount.1 --> string = float [possible delimiters: :, :=, =]
        temperature --> float
        pressure --> float
        additive.2 --> string
        amount.2 --> float
        where .1[.2] is index of additive. possible set multiple additives.

        example [RDF]:
        $DTYPE additive.amount.1
        $DATUM water = .4
        $DTYPE temperature
        $DATUM 298
        $DTYPE pressure
        $DATUM 0.9
        $DTYPE additive.2
        $DATUM DMSO
        $DTYPE amount.2
        $DATUM 0.6

        parsed as:
        additives = {'water': 0.4, 'DMSO': 0.6}
        temperature = 298
        pressure = 0.9
        """
        try:
            _type = TaskType(_type)
        except ValueError:
            abort(403, message='invalid task type [%s]. valid values are %s' % (_type, task_types_desc))

        args = uf_post.parse_args()

        if args['file.url'] and url(args['file.url']):
            # smart frontend
            file_url = args['file.url']
        elif args['file.path'] and path.exists(path.join(UPLOAD_PATH, path.basename(args['file.path']))):
            # NGINX upload
            file_url = url_for('.batch_file', file=path.basename(args['file.path']))
        elif args['structures']:
            # flask
            file_name = str(uuid.uuid4())
            args['structures'].save(path.join(UPLOAD_PATH, file_name))
            file_url = url_for('.batch_file', file=file_name)
        else:
            abort(400, message='structure file required')

        new_job = redis.new_job(dict(status=TaskStatus.NEW, type=_type, user=current_user.id,
                                     structures=[dict(data=dict(url=file_url), status=StructureStatus.RAW,
                                                      type=StructureType.UNDEFINED,
                                                      models=[get_model(ModelType.PREPARER)])]))
        if new_job is None:
            abort(500, message='modeling server error')

        return dict(task=new_job['id'], status=TaskStatus.PREPARING.value, type=_type.value,
                    date=new_job['created_at'].strftime("%Y-%m-%d %H:%M:%S"), user=current_user.id), 201


class LogIn(Resource):
    @swagger.operation(
        notes='App login',
        nickname='login',
        parameters=[dict(name='credentials', description='user credentials', required=True,
                         allowMultiple=False, dataType=LogInFields.__name__, paramType='body')],
        responseMessages=[dict(code=200, message="logged in"),
                          dict(code=400, message="invalid data"),
                          dict(code=403, message="bad credentials")])
    def post(self):
        """
        Get auth token

        Token returned in headers as remember_token.
        for use task api send in requests headers Cookie: 'remember_token=_token_'
        """
        data = request.get_json(force=True)
        if data:
            username = data.get('user')
            password = data.get('password')
            if username and password:
                user = UserLogin.get(username.lower(), password)
                if user:
                    login_user(user, remember=True)
                    return dict(message='logged in'), 200
        return dict(message='bad credentials'), 403


api.add_resource(CreateTask, '/task/create/<int:_type>')
api.add_resource(UploadTask, '/task/upload/<int:_type>')
api.add_resource(PrepareTask, '/task/prepare/<string:task>')
api.add_resource(ModelTask, '/task/model/<string:task>')
api.add_resource(ResultsTask, '/task/results/<string:task>')
api.add_resource(AvailableAdditives, '/resources/additives')
api.add_resource(AvailableModels, '/resources/models')
api.add_resource(RegisterModels, '/admin/models')
api.add_resource(LogIn, '/auth')
