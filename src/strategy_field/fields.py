# -*- coding: utf-8 -*-
import six
from inspect import isclass

import django
from django.core.exceptions import ValidationError
from django.core.validators import BaseValidator
from django.db import models
from django.db.models.fields import BLANK_CHOICE_DASH, NOT_PROVIDED
from django.utils.deconstruct import deconstructible
from django.utils.text import capfirst
from django.utils.translation import ugettext_lazy as _

from strategy_field.forms import (StrategyFormField,
                                  StrategyMultipleChoiceFormField,)
from strategy_field.utils import fqn, import_by_name, stringify

NOCONTEXT = object()


def get_class(value):
    if not value:
        pass
    elif isinstance(value, six.string_types):
        value = import_by_name(value)
    elif isclass(value):
        pass
    elif isinstance(value, object):
        value = type(value)
    else:
        raise ValueError(value)
    return value


@deconstructible
class ClassnameValidator(BaseValidator):
    message = _('Ensure this value is valid class name (it is %(show_value)s).')
    code = 'classname'

    def compare(self, a, b):
        try:
            get_class(a)
        except (ImportError, TypeError):
            return True
        return False


@deconstructible
class RegistryValidator(ClassnameValidator):
    message = _('Ensure this value is registered (it is %(show_value)s).')
    code = 'registry'

    def compare(self, value, registry):
        if value is None:
            return False
        elif isinstance(value, (list, tuple)):
            for c in value:
                if not issubclass(get_class(value), registry.klass):
                    return False
        else:
            value = get_class(value)
        return not issubclass(value, registry.klass)


class StrategyClassFieldDescriptor(object):
    def __init__(self, field):
        self.field = field

    def __get__(self, obj, type=None):
        value = obj.__dict__.get(self.field.name)
        try:
            return get_class(value)
        except:
            raise ValidationError(value)
            # if isclass(value):
            #     return value
            # elif isinstance(value, six.string_types):
            #     return import_by_name(value)

    def __set__(self, obj, value):
        obj.__dict__[self.field.name] = value


class MultipleStrategyClassFieldDescriptor(object):
    def __init__(self, field):
        self.field = field

    def __get__(self, obj, type=None):
        value = obj.__dict__.get(self.field.name)

        if isinstance(value, six.string_types):
            value = value.split(',')
        if not isinstance(value, (list, tuple)):
            value = [value] if value is not None else None  # GG: avoid [None]
        if isinstance(value, (list, tuple)):
            ret = []
            for v in value:
                if isinstance(v, six.string_types) and v:
                    v = import_by_name(v)
                ret.append(v)
            return ret

    def __set__(self, obj, value):
        obj.__dict__[self.field.name] = value


class AbstractStrategyField(models.Field):
    def __init__(self, *args, **kwargs):
        kwargs['max_length'] = 200

        self.registry = kwargs.pop("registry", None)
        super(AbstractStrategyField, self).__init__(*args, **kwargs)
        self.validators.append(ClassnameValidator(None))
        if self.registry:
            self.validators.append(RegistryValidator(self.registry))

    def contribute_to_class(self, cls, name, private_only=False, virtual_only=NOT_PROVIDED):
        self.set_attributes_from_name(name)
        self.model = cls
        cls._meta.add_field(self)
        setattr(cls, self.name, self.descriptor(self))

    def get_internal_type(self):
        return 'CharField'

    def _get_choices(self):
        if self.registry:
            return self.registry.as_choices()
        return []

    def _set_choices(self, value):
        pass

    choices = property(_get_choices, _set_choices)

    def get_choices(self, include_blank=True, blank_choice=BLANK_CHOICE_DASH, limit_choices_to=None):
        first_choice = blank_choice if include_blank else []
        return first_choice + [(k, k) for k, v in self.choices]

    def validate(self, value, model_instance):
        return value in self.registry

    def deconstruct(self):
        name, path, args, kwargs = super(
            AbstractStrategyField, self).deconstruct()
        del kwargs["max_length"]
        return name, path, args, kwargs

    def formfield(self, form_class=None, choices_form_class=None, **kwargs):
        defaults = {'required': not self.blank,
                    'label': capfirst(self.verbose_name),
                    'help_text': self.help_text,
                    'registry': self.registry}
        if self.has_default():
            if callable(self.default):
                defaults['initial'] = self.default
                defaults['show_hidden_initial'] = True
            else:
                defaults['initial'] = self.get_default()
        include_blank = (self.blank or
                         not (self.has_default() or 'initial' in kwargs))
        defaults['choices'] = self.get_choices(include_blank=include_blank)
        if self.null:
            defaults['empty_value'] = None
        form_class = choices_form_class or self.form_class
        for k in list(kwargs):
            if k not in ('empty_value', 'choices', 'required',
                         'registry',
                         'widget', 'label', 'initial', 'help_text',
                         'error_messages', 'show_hidden_initial'):
                del kwargs[k]
        defaults.update(kwargs)
        return form_class(**defaults)


class StrategyClassField(AbstractStrategyField):
    form_class = StrategyFormField
    descriptor = StrategyClassFieldDescriptor

    def get_prep_value(self, value):
        if value is None:
            return None
        # if isinstance(value, six.string_types):
        #     return value
        # if isclass(value) or isinstance(value, object):
        #     return fqn(value)
        return fqn(value)

    def get_prep_lookup(self, lookup_type, value):
        if lookup_type == 'exact':
            return self.get_prep_value(value)
        elif lookup_type == 'in':
            return [self.get_prep_value(v) for v in value]
        elif lookup_type == 'contains':
            return self.get_prep_value(value)
        elif lookup_type == 'icontains':
            return self.get_prep_value(value)
        else:
            raise TypeError('Lookup type %r not supported.' % lookup_type)


class MultipleStrategyClassField(AbstractStrategyField):
    descriptor = MultipleStrategyClassFieldDescriptor
    form_class = StrategyMultipleChoiceFormField

    def validate(self, value, model_instance):
        return value in self.registry

    def get_db_prep_save(self, value, connection):
        value = filter(lambda x: x, value) if value is not None else None  # GG: avoid the TypeError NoneType iterable
        return super(MultipleStrategyClassField, self).get_db_prep_save(value, connection)

    def get_prep_value(self, value):
        if value is None:
            return None
        elif isinstance(value, (list, tuple)):
            return stringify(value)
        elif isinstance(value, six.string_types):
            return value

    def get_prep_lookup(self, lookup_type, value):
        if lookup_type == 'exact':
            return self.get_prep_value(value)
        elif lookup_type == 'in':
            raise TypeError('Lookup type %r not supported.' % lookup_type)
        elif lookup_type == 'icontains':
            return self.get_prep_value(value)
        elif lookup_type == 'contains':
            return self.get_prep_value(value)

    def get_lookup(self, lookup_name):
        if lookup_name == 'in':
            raise TypeError('Lookup type %r not supported.' % lookup_name)
        return super(MultipleStrategyClassField, self).get_lookup(lookup_name)

    def get_choices(self, include_blank=True, blank_choice=BLANK_CHOICE_DASH, limit_choices_to=None):
        return AbstractStrategyField.get_choices(self, False, blank_choice)


class StrategyFieldDescriptor(StrategyClassFieldDescriptor):
    def __get__(self, obj, value=None):
        return obj.__dict__.get(self.field.name)

    def __set__(self, obj, value):
        if not value:
            value = None
        else:
            value = get_class(value)
        # elif isinstance(value, six.string_types):
        #     value = import_by_name(value)
        # elif isclass(value):
        #     value = value

        if isclass(value):
            value = self.field.factory(value, obj)

        obj.__dict__[self.field.name] = value


class StrategyField(StrategyClassField):
    descriptor = StrategyFieldDescriptor

    def __init__(self, *args, **kwargs):
        self.factory = kwargs.pop('factory', lambda klass, obj: klass(obj))
        super(StrategyField, self).__init__(*args, **kwargs)

    def pre_save(self, model_instance, add):
        value = getattr(model_instance, self.attname)
        if value:
            return fqn(value)


class MultipleStrategyFieldDescriptor(MultipleStrategyClassFieldDescriptor):
    def __get__(self, obj, type=None):
        value = obj.__dict__.get(self.field.name)

        if isinstance(value, six.string_types) or isinstance(value, (list, tuple)):
            ret = []
            if isinstance(value, six.string_types):
                value = value.split(',')
            for v in value:
                if isinstance(v, six.string_types):
                    v = import_by_name(v)

                if isclass(v):
                    ret.append(self.field.factory(v, obj))
                else:
                    ret.append(v)

            return ret

    def __set__(self, obj, value):
        obj.__dict__[self.field.name] = value


class MultipleStrategyField(MultipleStrategyClassField):
    descriptor = MultipleStrategyFieldDescriptor

    def __init__(self, *args, **kwargs):
        self.factory = kwargs.pop('factory', lambda klass, obj: klass(obj))
        super(MultipleStrategyField, self).__init__(*args, **kwargs)

    def get_lookup(self, lookup_name):
        if lookup_name == 'in':
            raise TypeError('Lookup type %r not supported.' % lookup_name)
        return super(MultipleStrategyField, self).get_lookup(lookup_name)


if django.VERSION[:2] >= (1, 10):
    from django.db.models.lookups import Contains, In, IContains

    class StrategyFieldLookupMixin(object):
        def get_prep_lookup(self):
            value = super(StrategyFieldLookupMixin, self).get_prep_lookup()
            if value is None:
                return None
            if isinstance(value, six.string_types):
                pass
            elif isinstance(value, (list, tuple)):
                value = stringify(value)
            elif isinstance(value, self.lhs.output_field.registry.klass):
                value = fqn(value)
            elif isclass(value) or isinstance(value, object):
                value = fqn(value)
            return value

    class StrategyFieldContains(StrategyFieldLookupMixin, IContains):
        pass

    class StrategyFieldIContains(StrategyFieldLookupMixin, Contains):
        pass

    class StrategyFieldIn(StrategyFieldLookupMixin, In):
        pass

    class MultipleStrategyFieldContains(StrategyFieldLookupMixin, Contains):
        pass

    class MultipleStrategyFieldIn(StrategyFieldLookupMixin, In):
        pass

    StrategyField.register_lookup(StrategyFieldContains)
    StrategyField.register_lookup(StrategyFieldIContains)
    MultipleStrategyField.register_lookup(MultipleStrategyFieldContains)

    StrategyClassField.register_lookup(StrategyFieldContains)
    StrategyClassField.register_lookup(StrategyFieldIContains)
    MultipleStrategyClassField.register_lookup(MultipleStrategyFieldContains)
