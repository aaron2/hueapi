#!/usr/bin/python

import requests
import json
import socket
import httplib
import StringIO

class SSDPResponse(object):
    class _FakeSocket(StringIO.StringIO):
        def makefile(self, *args, **kw):
            return self
    def __init__(self, response):
        r = httplib.HTTPResponse(self._FakeSocket(response))
        r.begin()
        self.description = r.getheader("location")
        self.usn = r.getheader("usn")
        self.st = r.getheader("st")
        self.ip = self.description.split('/')[2].split(':')[0]
        self.cache = r.getheader("cache-control").split("=")[1]
    def __repr__(self):
        return "<SSDPResponse({description}, {st}, {usn})>".format(**self.__dict__)

def discover(timeout=5, retries=1, mx=3):
    group = ("239.255.255.250", 1900)
    message = "\r\n".join([
        'M-SEARCH * HTTP/1.1',
        'HOST: {0}:{1}',
        'MAN: "ssdp:discover"',
        'ST: {st}','MX: {mx}','',''])
    socket.setdefaulttimeout(timeout)
    responses = {}
    for _ in range(retries):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.sendto(message.format(*group, st='hue', mx=mx), group)
        while True:
            try:
                response = SSDPResponse(sock.recv(1024))
                responses[response.description] = response
            except socket.timeout:
                break
    return responses.values()

class Hue(object):
  def __init__(self, addr, key):
    self.addr = addr
    self.key = key

  def _call(self, method, path, data):
    #print method, 'http://{}/api/{}{}'.format(self.addr, self.key, path), json.dumps(data, indent=4)
    res = requests.request(method, 'http://{}/api/{}{}'.format(self.addr, self.key, path), data=json.dumps(data))
    res.raise_for_status()
    js = res.json()
    if type(js) == list and len(js) > 0 and 'error' in js[0]:
      print method, 'http://{}/api/{}{}'.format(self.addr, self.key, path), json.dumps(data, indent=4)
      raise ValueError(js)
    #print json.dumps(res.json(), indent=4)
    #print ""
    return res.json()

  def _put(self, path, data):
    return self._call('put', path, data)

  def _post(self, path, data):
    return self._call('post', path, data)

  def _delete(self, path):
    return self._call('delete', path, {})

  def _get(self, path):
    return self._call('get', path, {})

  def lights(self):
    return self._get('/lights')

  def groups(self, name=None):
    return [Group(self, id, params) for id,params in self._get('/groups').items() if name == None or name == params['name']]

  def config(self):
    return self._get('/config')

  def schedules(self):
    return self._get('/schedules')

  def scenes(self, name=None):
    return [Scene(self, id, params) for id,params in self._get('/scenes').items() if name == None or name == params['name']]

  def create_scene(self, data):
    res = self._post('/scenes', data)
    return Scene(self, res[0]['success']['id'], data)

  def create_rule(self, data):
    res = self._post('/rules', data)
    return Rule(self, res[0]['success']['id'], data)

  def sensors(self, name=None):
    return [Sensor(self, id, params) for id,params in self._get('/sensors').items() if name == None or name == params['name']]

  def rules(self, name=None, action=None, condition=None):
    return [Rule(self, id, params) for id,params in self._get('/rules').items() if (action == None or action in params['actions']) and (condition == None or condition in params['conditions']) and (name == None or name == params['name'])]

  def make_action(self, address, method, body):
    return { 'body': body, 'method': method, 'address': address }

  def ensure_scene(self, scene_name, group, params):
    scene = self.scenes(scene_name)
    if scene:
      return scene[0]
    scene = self.create_scene({'recycle': True, 'name': scene_name, 'lights': group.lights})
    for light in scene.lights:
      scene.lightstate(light, params)
    return scene
  
  def find_switch_for_group(self, group):
    sensors = self.sensors()
    found_sensors = {}
    for r in self.rules():
      for a in r.actions:
        if a['address'] == group.action_path:
          for c in r.conditions:
            if c['address'].startswith('/sensors/'):
              found_sensors[c['address'].split('/')[2]] = True
  
    switch = None
    for f in found_sensors.keys():
      for s in sensors:
        if s.id == f and s.type == 'ZLLSwitch':
          switch = s
    return switch
  
  def reset_switch_rules(self, switch, group):
    for r in self.rules():
      for c in r.conditions:
        if c['address'].startswith(switch.buttonevent_path):
          r.delete()
          continue
  
    cycle = self.sensors('Dimmer Switch 2 SceneCycle')[0]
  
    self.create_rule({
      'name': switch.name+' dn-long',
      'conditions': [
        switch.make_condition(switch.buttonevent_path, 'eq', '3001'),
        switch.make_condition(switch.lastupdated_path, 'dx'),
      ],
      'actions': [ group.make_action('PUT', { 'transitiontime': 9, 'bri_inc': -56 }) ],
    })
    self.create_rule({
      'name': switch.name+' dn-press',
      'conditions': [
        switch.make_condition(switch.buttonevent_path, 'eq', '3000'),
        switch.make_condition(switch.lastupdated_path, 'dx'),
      ],
      'actions': [ group.make_action('PUT', { 'transitiontime': 9, 'bri_inc': -30 }) ],
    })
    self.create_rule({
      'name': switch.name+' reset timer',
      'conditions': [
        switch.make_condition(switch.lastupdated_path, 'dx')
      ],
      'actions': [ self.make_action('/schedules/1', 'PUT', { 'status': 'enabled', 'localtime': 'PT00:00:10' }) ],
    })
    self.create_rule({
      'name': switch.name+' dn-rele',
      'conditions': [
        switch.make_condition(switch.buttonevent_path, 'eq', '3003'),
        switch.make_condition(switch.lastupdated_path, 'dx'),
      ],
      'actions': [ group.make_action('PUT', { 'bri_inc': 0 }) ],
    })
    self.create_rule({
      'name': switch.name+' on0',
      'conditions': [
        switch.make_condition(switch.buttonevent_path, 'eq', '1000'),
        switch.make_condition(switch.lastupdated_path, 'dx'),
        cycle.make_condition(cycle.status_path, 'lt', '1'),
      ],
      'actions': [
        group.make_action('PUT', { 'on': True }),
        cycle.make_action('PUT', { 'status': 1 })
      ],
    })
    self.create_rule({
      'name': switch.name+' up-press',
      'conditions': [
        switch.make_condition(switch.buttonevent_path, 'eq', '2000'),
        switch.make_condition(switch.lastupdated_path, 'dx'),
      ],
      'actions': [ group.make_action('PUT', { 'transitiontime': 9, 'bri_inc': 30 }), ]
    })
    self.create_rule({
      'name': switch.name+' off',
      'conditions': [
        switch.make_condition(switch.buttonevent_path, 'eq', '4000'),
        switch.make_condition(switch.lastupdated_path, 'dx'),
      ],
      'actions': [
        group.make_action('PUT', { 'on': False }),
        cycle.make_action('PUT', { 'status': 0 }),
      ]
    })
    self.create_rule({
      'name': switch.name+' up-rele',
      'conditions': [
        switch.make_condition(switch.buttonevent_path, 'eq', '2003'),
        switch.make_condition(switch.lastupdated_path, 'dx'),
      ],
      'actions': [ group.make_action('PUT', { 'bri_inc': 0 }), ]
    })
    self.create_rule({
      'name': switch.name+' up-long',
      'conditions': [
        switch.make_condition(switch.buttonevent_path, 'eq', '2001'),
        switch.make_condition(switch.lastupdated_path, 'dx'),
      ],
      'actions': [ group.make_action('PUT', { 'transitiontime': 9, 'bri_inc': 56 }), ]
    })

class GenericHueObject(Hue):
  def __init__(self, hue, id, params):
    for k,v in params.items():
      self.__dict__[k] = v
    self.__dict__['id'] = id
    self.__dict__['hue'] = hue
  def __repr__(self):
    return json.dumps({k:v for k,v in self.__dict__.items() if k != "hue"}, indent=4)

class Group(GenericHueObject):
  def __init__(self, hue, id, params):
    super(self.__class__, self).__init__(hue, id, params)
    self.__dict__['action_path'] = '/groups/'+self.id+'/action'
    for k,v in params.items():
      self.__dict__[k] = v
  def __setattr__(self, k, v):
    self.hue._put(self.action_path, {k: v})
    self.__dict__[k] = v
  def make_action(self, method, body, address=None):
    return { 'body': body, 'method': method, 'address': self.action_path }

class Scene(GenericHueObject):
  #def __setattr__(self, k, v):
  #  print self.hue._put('/scenes/'+self.id+'/lightstates/1', {k: v})
  #  self.__dict__[k] = v
  def get(self):
    return self.hue._get('/scenes/'+self.id)
  def delete(self):
    return self.hue._delete('/scenes/'+self.id)
  def lightstate(self, light_id, params):
    return self.hue._put('/scenes/'+self.id+'/lightstates/'+light_id, params)

class Rule(GenericHueObject):
  def __setattr__(self, k, v):
    res = self.hue._put('/rules/'+self.id, {k: v})
    if 'success' in res[0]:
        self.__dict__[k] = v
    return res
  def update(self, params):
    return self.hue._put('/rules/'+self.id+'/action', params)
  def delete(self):
    return self.hue._delete('/rules/'+self.id)

class Sensor(GenericHueObject):
  def __init__(self, hue, id, params):
    super(self.__class__, self).__init__(hue, id, params)
    self.__dict__['buttonevent_path'] = '/sensors/'+self.id+'/state/buttonevent'
    self.__dict__['lastupdated_path'] = '/sensors/'+self.id+'/state/lastupdated'
    self.__dict__['status_path'] = '/sensors/'+self.id+'/state/status'
  def __setattr__(self, k, v):
    self.hue._put('/sensors/'+self.id, {k: v})
    self.__dict__[k] = v
  def make_action(self, method, body):
    return { 'body': body, 'method': method, 'address': '/sensors/'+self.id+'/state' }
  def make_condition(self, address, operator, value=None):
    if value:
      return { 'operator': operator, 'value': value, 'address': address }
    return { 'operator': operator, 'address': address }

