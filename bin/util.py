# read problem settings from config files

import shutil
import config
import time
import copy
import yaml
import subprocess
import sys
import os
import re
import yaml
import threading

from pathlib import Path


def is_windows():
    return sys.platform in ['win32', 'cygwin']


if not is_windows():
    import resource


# color printing
class Colorcodes(object):
    def __init__(self):
        if not is_windows():
            self.bold = '\033[;1m'
            self.reset = '\033[0;0m'
            self.blue = '\033[;96m'
            self.green = '\033[;32m'
            self.orange = '\033[;33m'
            self.red = '\033[;31m'
            self.white = '\033[;39m'

            self.boldblue = '\033[1;34m'
            self.boldgreen = '\033[1;32m'
            self.boldorange = '\033[1;33m'
            self.boldred = '\033[1;31m'
        else:
            self.bold = ''
            self.reset = ''
            self.blue = ''
            self.green = ''
            self.orange = ''
            self.red = ''
            self.white = ''

            self.boldblue = ''
            self.boldgreen = ''
            self.boldorange = ''
            self.boldred = ''


cc = Colorcodes()


def log(msg):
    print(cc.green + 'LOG: ' + msg + cc.reset)


def warn(msg):
    print(cc.orange + 'WARNING: ' + msg + cc.reset)
    config.n_warn += 1


def error(msg):
    print(cc.red + 'ERROR: ' + msg + cc.reset)
    config.n_error += 1


def fatal(msg):
    print(cc.red + 'FATAL: ' + msg + cc.reset)
    exit(1)


# A class that draws a progressbar.
# Construct with a constant prefix, the max length of the items to process, and
# the number of items to process.
# When count is None, the bar itself isn't shown.
# Start each loop with bar.start(current_item), end it with bar.done(message).
# Optionally, multiple errors can be logged using bar.log(error). If so, the
# final message on bar.done() will be ignored.
class ProgressBar:
    # Lock on all IO via this class.
    lock = threading.Lock()

    def __init__(self, prefix, max_len=None, count=None, *, items=None):
        assert not (items and (max_len or count))
        if items:
            count = len(items)
            max_len = max(len(str(x) if isinstance(x, str) else x.name) for x in items)
        self.prefix = prefix  # The prefix to always print
        self.item_width = max_len  # The max length of the items we're processing
        self.count = count  # The number of items we're processing
        self.i = 0
        self.carriage_return = '\r' if is_windows() else '\033[K'
        self.global_logged = False

        # For parallel contexts, start() will return a copy to preserve the item name.
        # The parent still holds some global state:
        # - global_logged
        # - IO lock
        # - the counter
        # - items in progress
        self.parent = None
        self.in_progress = set()
        self.item = None

    def total_width(self):
        return shutil.get_terminal_size().columns

    def bar_width(self):
        if self.item_width is None: return None
        return self.total_width() - len(self.prefix) - 2 - self.item_width - 1

    def update(self, count, max_len):
        self.count += count
        self.item_width = max(self.item_width, max_len) if self.item_width else max_len

    def add_item(self, item):
        self.count += 1
        self.item_width = max(self.item_width, len(str(item)))

    def clearline(self):
        if hasattr(config.args, 'no_bar') and config.args.no_bar: return
        print(self.carriage_return, end='', flush=True)

    def action(prefix, item, width=None, total_width=None):
        if width is not None and total_width is not None and len(prefix) + 2 + width > total_width:
            width = total_width - len(prefix) - 2
        item = '' if item is None else (item if isinstance(item, str) else item.name)
        if width is not None and len(item) > width: item = item[:width]
        if width is None: width = 0
        return f'{cc.blue}{prefix}{cc.reset}: {item:<{width}}'

    def get_prefix(self):
        return ProgressBar.action(self.prefix, self.item, self.item_width, self.total_width())

    def get_bar(self):
        bar_width = self.bar_width()
        if self.count is None or bar_width < 4: return ''
        fill = (self.i - 1) * (bar_width - 2) // self.count
        return '[' + '#' * fill + '-' * (bar_width - 2 - fill) + ']'

    # Remove the current item from in_progress.
    def _release_item(self):
        if self.parent:
            self.parent.in_progress.remove(self.item)
        else:
            self.in_progress.remove(self.item)
        self.item = None

    # Resume the ongoing progress bar after a log/done.
    # Should only be called for the root.
    def _resume(self):
        assert self.lock.locked()
        assert self.parent is None

        if config.args.no_bar: return

        if len(self.in_progress) > 0:
            if not self.item in self.in_progress:
                old = self.item
                self.item = next(iter(self.in_progress))
            bar = self.get_bar()
            if bar is None or bar == '':
                print(self.get_prefix(), end='\r', flush=True)
            else:
                print(self.get_prefix(), bar, end='\r', flush=True)

    def start(self, item=''):
        self.lock.acquire()
        # start may only be called on the root bar.
        assert self.parent is None
        self.i += 1
        assert self.count is None or self.i <= self.count

        assert self.item is None
        self.item = item
        self.logged = False
        self.in_progress.add(item)
        bar_copy = copy.copy(self)
        bar_copy.parent = self

        if config.args.no_bar:
            self.lock.release()
            return bar_copy

        bar = self.get_bar()
        if bar is None or bar == '':
            print(self.get_prefix(), end='\r', flush=True)
        else:
            print(self.get_prefix(), bar, end='\r', flush=True)

        self.lock.release()
        return bar_copy

    @staticmethod
    def _format_data(data):
        if not data: return ''
        prefix = '  ' if data.count('\n') <= 1 else '\n'
        return prefix + cc.orange + strip_newline(data) + cc.reset

    # Done can be called multiple times to make multiple persistent lines.
    # Make sure that the message does not end in a newline.
    def log(self, message='', data='', color=cc.green, *, needs_lock=True):
        if needs_lock: self.lock.acquire()

        if message is None: message = ''
        self.clearline()
        self.logged = True
        if self.parent: self.parent.global_logged = True
        else: self.global_logged = True
        print(self.get_prefix() +
              color + message + ProgressBar._format_data(data) + cc.reset,
              flush=True)

        if self.parent: self.parent._resume()

        if needs_lock: self.lock.release()

    def warn(self, message='', data=''):
        config.n_warn += 1
        self.log(message, data, cc.orange)

    # Error removes the current item from the in_progress set.
    def error(self, message='', data=''):
        self.lock.acquire()
        config.n_error += 1
        self.log(message, data, cc.red, needs_lock=False)
        self._release_item()
        self.lock.release()

    # Log a final line if it's an error or if nothing was printed yet and we're in verbose mode.
    def done(self, success=True, message='', data=''):
        self.lock.acquire()
        self.clearline()

        if self.item is None:
            self.lock.release()
            return

        if self.logged:
            self._release_item()
            self.lock.release()
            return

        if not success: config.n_error += 1

        do_print = config.verbose or not success
        if do_print:
            self.log(message, data, needs_lock=False, color= cc.green if success else cc.red)

        self._release_item()
        if self.parent: self.parent._resume()

        self.lock.release()
        return

    # Log an intermediate line if it's an error or we're in verbose mode.
    # Return True when something was printed
    def part_done(self, success=True, message='', data=''):
        self.clearline()
        if not success: config.n_error += 1
        if config.verbose or not success:
            if success:
                self.log(message, data)
            else:
                self.error(message, data)
            return True
        return False

    # Print a final 'Done' message in case nothing was printed yet.
    # When 'message' is set, always print it.
    def finalize(self, *, print_done=True, message=None):
        assert self.parent is None
        assert self.count is None or self.i == self.count
        assert self.item is None

        if not print_done and message is None: return

        if message is None:
            message = f'{cc.green}Done{cc.reset}'
            if self.global_logged: return
            if config.verbose: return

        print(self.get_prefix() + message)


# Drops the first two path components <problem>/<type>/
def print_name(path, keep_type=False):
    return str(Path(*path.parts[1 if keep_type else 2:]))


def read_yaml(path):
    settings = {}
    if path.is_file():
        with path.open() as yamlfile:
            try:
                config = yaml.safe_load(yamlfile)
            except:
                warn(f'Failed to parse {path}. Using defaults.')
                return {}
            if config is None: return None
            if isinstance(config, list): return config
            for key, value in config.items():
                settings[key] = '' if value is None else value
    return settings


def is_hidden(path):
    for d in path.parts:
        if d[0] == '.':
            return True
    return False


def is_template(path):
    return path.suffix == '.template'


# glob, but without hidden files
def glob(path, expression):
    return sorted(p for p in path.glob(expression) if not is_hidden(p) and not is_template(p))



def strip_newline(s):
    if s.endswith('\n'):
        return s[:-1]
    else:
        return s


# When output is True, copy the file when args.cp is true.
def ensure_symlink(link, target, output=False, relative=False):
    if output and hasattr(config.args, 'cp') and config.args.cp == True:
        if link.exists() or link.is_symlink(): link.unlink()
        shutil.copyfile(target, link)
        return

    # Do nothing if link already points to the right target.
    if link.is_symlink() and link.resolve() == target.resolve():
        is_absolute = os.readlink(link)
        if not relative and is_absolute: return
        #if relative and not is_absolute: return

    if link.is_symlink() or link.exists():
        link.unlink()
    if relative:
        # Rewrite target to be relative to link.
        rel_target = os.path.relpath(target, link.parent)
        os.symlink(rel_target, link)
    else:
        link.symlink_to(target.resolve())


def substitute(data, variables):
    for key in variables:
        r = ''
        if variables[key] != None: r = variables[key]
        data = data.replace('{%' + key + '%}', str(r))
    return data


def copy_and_substitute(inpath, outpath, variables):
    try:
        data = inpath.read_text()
    except UnicodeDecodeError:
        # skip this file
        warn(f'File "{inpath}" has no unicode encoding.')
        return
    data = substitute(data, variables)
    if outpath.is_symlink():
        outpath.unlink()
    outpath.write_text(data)


def substitute_file_variables(path, variables):
    copy_and_substitute(path, path, variables)


def substitute_dir_variables(dirname, variables):
    for path in dirname.rglob('*'):
        if path.is_file():
            substitute_file_variables(path, variables)


# copies a directory recursively and substitutes {%key%} by their value in text files
# reference: https://docs.python.org/3/library/shutil.html#copytree-example
def copytree_and_substitute(src, dst, variables, exist_ok=True):
    names = os.listdir(src)
    os.makedirs(dst, exist_ok=exist_ok)
    errors = []
    for name in names:
        try:
            srcFile = src / name
            dstFile = dst / name

            if os.path.islink(srcFile):
                shutil.copy(srcFile, dstFile, follow_symlinks=False)
            elif (os.path.isdir(srcFile)):
                copytree_and_substitute(srcFile, dstFile, variables, exist_ok)
            elif (dstFile.exists()):
                warn(f'File "{dstFile}" already exists, skipping...')
                continue
            else:
                try:
                    data = srcFile.read_text()
                    data = substitute(data, variables)
                    dstFile.write_text(data)
                except UnicodeDecodeError:
                    # skip this file
                    warn(f'File "{srcFile}" has no unicode encoding.')
                    dstFile.write_bytes(srcFile.read_bytes())
        except OSError as why:
            errors.append((srcFile, dstFile, str(why)))
        # catch the Error from the recursive copytree so that we can
        # continue with other files
        except Error as err:
            errors.extend(err.args[0])
    if errors:
        raise Error(errors)


def crop_output(output):
    if config.args.noerror: return None
    if config.args.error: return output

    lines = output.split('\n')
    numlines = len(lines)
    cropped = False
    # Cap number of lines
    if numlines > 10:
        output = '\n'.join(lines[:8])
        output += '\n'
        cropped = True

    # Cap line length.
    if len(output) > 200:
        output = output[:200]
        output += ' ...\n'
        cropped = True

    if cropped:
        output += cc.orange + 'Use -e to show more or -E to hide it.' + cc.reset
    return output


# Return memory limit in bytes.
def get_memory_limit(kwargs=None):
    memory_limit = 4000000000  # 4GB
    if hasattr(config.args, 'memory'):
        if config.args.memory and config.args.memory != 'unlimited':
            memory_limit = int(config.args.memory)
    if kwargs and 'memory' in kwargs:
        memory_limit = kwargs['memory']
        kwargs.pop('memory')
    return memory_limit




# Run `command`, returning stderr if the return code is unexpected.
# TODO: Make this return an ExecResult object containing the return code/status, the time, and stdout/stderr.
def exec_command(command, expect=0, crop=True, **kwargs):
    # By default: discard stdout, return stderr
    if 'stdout' not in kwargs or kwargs['stdout'] is True: kwargs['stdout'] = subprocess.PIPE
    if 'stderr' not in kwargs or kwargs['stderr'] is True: kwargs['stderr'] = subprocess.PIPE

    # Convert any Pathlib objects to string.
    command = [str(x) for x in command]

    if config.verbose >= 2:
        print(command, kwargs, 'cwd:', Path.cwd())

    timeout = 30
    if 'timeout' in kwargs:
        if kwargs['timeout']:
            timeout = kwargs['timeout']
        kwargs.pop('timeout')

    memory_limit = get_memory_limit(kwargs)

    # Disable memory limits for Java.
    # TODO: Also disable this for Kotlin.
    if command[0] in ['java', 'javac', 'kotlin', 'kotlinc']:
        memory_limit = None

    # Note: Resource limits do not work on windows.
    def setlimits():
        resource.setrlimit(resource.RLIMIT_CPU, (timeout + 1, timeout + 1))
        # Increase the max stack size from default to the max available.
        if sys.platform != 'darwin':
            resource.setrlimit(resource.RLIMIT_STACK,
                               (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
        if memory_limit:
            resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))

    if not is_windows():
        process = subprocess.Popen(command, preexec_fn=setlimits, **kwargs)
    else:
        process = subprocess.Popen(command, **kwargs)
    try:
        (stdout, stderr) = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        (stdout, stderr) = process.communicate()
    except KeyboardInterrupt:
        fatal('Running interrupted.')

    def maybe_crop(s):
        return crop_output(s) if crop else s

    return (True if process.returncode == expect else process.returncode,
            maybe_crop(stderr.decode('utf-8')) if stderr is not None else None,
            maybe_crop(stdout.decode('utf-8')) if stdout is not None else None)

class ExecResult:
    # TODO: Replace ok by returncode and expected_returncode
    def __init__(self, ok , duration, err, out):
        self.ok = ok
        self.duration = duration
        self.err = err
        self.out = out

# TODO: Replace exec_command by this, which returns ExecResult.
def exec_command_2(command, expect=0, crop=True, **kwargs):
    tstart = time.monotonic()
    ok, err, out = exec_command(command, expect, crop, **kwargs)
    tend = time.monotonic()
    return ExecResult(ok, tend-tstart, err, out)
