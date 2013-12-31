class Instance(object):
    def __init__(self, name=None, id=None):
        self.id = id
        self.name = name


class Platform(object):
    def __init__(self, config, log_success, log_info, log_warn, log_error,
                 execute_handler=None, run_handler=None,
                 host_broker_handler=None, rexists=None):
        self._config = config

        self.log_success = log_success
        self.log_info = log_info
        self.log_warn = log_warn
        self.log_error = log_error
        self.execute_handler = execute_handler
        self.run_handler = run_handler
        self.host_broker_handler = host_broker_handler
        self.rexists = rexists

        if hasattr(self, '_on_init'):
            self._on_init()

    def config(self, name, default=None):
        return self._config.get(name, default)

    def _validate_config(self):
        raise Exception("Method not implemented")

    def find_instance(self, name):
        raise Exception("Method not implemented")

    def get_instance(self, id):
        raise Exception("Method not implemented")

    def create_instance(self, instance):
        raise Exception("Method not implemented")

    def reimage_instance(self, instance):
        raise Exception("Method not implemented")

    def instance_ready(self, instance):
        raise Exception("Method not implemented")
