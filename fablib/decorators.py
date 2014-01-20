from functools import wraps


def retry(times=5):
    """A decorator which retries tasks after a SystemExit exception has been
    thrown. This combats the fail-fast nature of Fabric in hopes of recovering
    a remote operation."""
    def real_retry(func):
        func.attempts = 0
        func.attempts_max = times

        @wraps(func)
        def wrapped(*args, **kwargs):
            while func.attempts < func.attempts_max:
                try:
                    return func(*args, **kwargs)
                except SystemExit:
                    func.attempts += 1

            # If we've reached this point, blanket raise whatever exception
            # brought us here.
            raise
        return wrapped
    return real_retry
