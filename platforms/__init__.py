from importlib import import_module


def load_manager(namespace, class_name):
    try:
        module = import_module(namespace)
        manager = getattr(module, class_name)
    except Exception:
        raise ImportError('Platform manager "%s.%s" does not exist.' %
                          (namespace, class_name))

    return manager
