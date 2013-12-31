from fabric.api import puts, green, yellow, red


def log_success(*args):
    puts(green(*args))


def log_info(*args):
    puts(yellow(*args))


def log_warn(*args):
    puts(yellow(*args))


def log_error(*args):
    puts(red(*args))
