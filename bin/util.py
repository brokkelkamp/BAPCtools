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
import signal

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


def debug(*msg):
    print(cc.blue, end='')
    print('DEBUG:', *msg, end='')
    print(cc.reset)


def log(msg):
    print(cc.green + 'LOG: ' + msg + cc.reset)


def warn(msg):
    print(cc.orange + 'WARNING: ' + msg + cc.reset)
    config.n_warn += 1


def error(msg):
    print(cc.red + 'ERROR: ' + msg + cc.reset)
    config.n_error += 1


def fatal(msg):
    print(cc.red + 'FATAL ERROR: ' + msg + cc.reset)
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

    current_bar = None

    @staticmethod
    def item_len(item):
        if isinstance(item, str): return len(item)
        if isinstance(item, Path): return len(str(item))
        return len(item.name)

    # When needs_leading_newline is True, this will print an additional empty line before the first log message.
    def __init__(self,
                 prefix,
                 max_len=None,
                 count=None,
                 *,
                 items=None,
                 needs_leading_newline=False):
        assert ProgressBar.current_bar is None
        ProgressBar.current_bar = self

        assert not (items and (max_len or count))
        assert items is not None or max_len
        assert items is not None or count is not None
        if items is not None:
            count = len(items)
            if count == 0:
                max_len = 0
            else:
                max_len = max(ProgressBar.item_len(x) for x in items)
        self.prefix = prefix  # The prefix to always print
        self.item_width = max_len + 1  # The max length of the items we're processing
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

        self.needs_leading_newline = needs_leading_newline

    def total_width(self):
        return shutil.get_terminal_size().columns

    def bar_width(self):
        if self.item_width is None: return None
        return self.total_width() - len(self.prefix) - 2 - self.item_width

    def update(self, count, max_len):
        self.count += count
        self.item_width = max(self.item_width, max_len + 1) if self.item_width else max_len + 1

    def add_item(self, item):
        self.count += 1
        self.item_width = max(self.item_width, ProgressBar.item_len(item))

    def clearline(self):
        if hasattr(config.args, 'no_bar') and config.args.no_bar: return
        assert self.lock.locked()
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
            if self.parent.item is self.item:
                self.parent.item = None
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
            p = None
            if not self.item in self.in_progress:
                old = self.item
                self.item = next(iter(self.in_progress))
                p = self.item
            bar = self.get_bar()
            if bar is None or bar == '':
                print(self.get_prefix(), end='\r', flush=True)
            else:
                print(self.get_prefix(), bar, sep='', end='\r', flush=True)

    def start(self, item=''):
        self.lock.acquire()
        # start may only be called on the root bar.
        assert self.parent is None
        self.i += 1
        assert self.count is None or self.i <= self.count

        #assert self.item is None
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
            print(self.get_prefix(), bar, sep='', end='\r', flush=True)

        self.lock.release()
        return bar_copy

    @staticmethod
    def _format_data(data):
        if not data: return ''
        prefix = '  ' if data.count('\n') <= 1 else '\n'
        return prefix + cc.orange + strip_newline(crop_output(data)) + cc.reset

    # Done can be called multiple times to make multiple persistent lines.
    # Make sure that the message does not end in a newline.
    def log(self, message='', data='', color=cc.green, *, needs_lock=True, resume=True):
        if needs_lock: self.lock.acquire()

        if message is None: message = ''
        self.clearline()
        self.logged = True
        if self.parent: self.parent.global_logged = True
        else: self.global_logged = True

        if self.needs_leading_newline:
            print()
            self.needs_leading_newline = False

        print(self.get_prefix(),
              color,
              message,
              ProgressBar._format_data(data),
              cc.reset,
              sep='',
              flush=True)

        if resume:
            if self.parent:
                self.parent._resume()
            else:
                self._resume()

        if needs_lock: self.lock.release()

    def warn(self, message='', data=''):
        config.n_warn += 1
        self.log(message, data, cc.orange)

    # Error removes the current item from the in_progress set.
    def error(self, message='', data='', needs_lock=True):
        if needs_lock: self.lock.acquire()
        config.n_error += 1
        self.log(message, data, cc.red, needs_lock=False, resume=False)
        self._release_item()
        if needs_lock: self.lock.release()

    # Log a final line if it's an error or if nothing was printed yet and we're in verbose mode.
    def done(self, success=True, message='', data=''):
        self.lock.acquire()
        self.clearline()

        if self.item is None:
            self.lock.release()
            return

        if not self.logged:
            if not success: config.n_error += 1
            if config.args.verbose or not success:
                self.log(message, data, needs_lock=False, color=cc.green if success else cc.red)

        self._release_item()
        if self.parent:
            self.parent._resume()

        self.lock.release()
        return

    # Log an intermediate line if it's an error or we're in verbose mode.
    # Return True when something was printed
    def part_done(self, success=True, message='', data=''):
        if not success: config.n_error += 1
        if config.args.verbose or not success:
            self.lock.acquire()
            if success:
                self.log(message, data, needs_lock=False)
            else:
                self.error(message, data, needs_lock=False)
            if self.parent:
                self.parent._resume()
            self.lock.release()
            return True
        return False

    # Print a final 'Done' message in case nothing was printed yet.
    # When 'message' is set, always print it.
    def finalize(self, *, print_done=True, message=None):
        self.lock.acquire()
        self.clearline()
        assert self.parent is None
        assert self.count is None or self.i == self.count
        assert self.item is None
        # At most one of print_done and message may be passed.
        if message: assert print_done is True

        # If nothing was logged, we don't need the super wide spacing before the final 'DONE'.
        if not self.global_logged and not message:
            self.item_width = 0

        # Print 'DONE' when nothing was printed yet but a summary was requested.
        if print_done and not self.global_logged and not message:
            message = f'{cc.green}Done{cc.reset}'

        if message:
            print(self.get_prefix(), message, sep='')

        # When something was printed, add a newline between parts.
        if self.global_logged:
            print()

        self.lock.release()

        assert ProgressBar.current_bar is not None
        ProgressBar.current_bar = None

        return self.global_logged


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
                fatal(f'Failed to parse {path}.')
            if config is None: return None
            if isinstance(config, list): return config
            for key, value in config.items():
                settings[key] = '' if value is None else value
    return settings


# glob, but without hidden files
def glob(path, expression):
    def keep(p):
        for d in p.parts:
            if d[0] == '.':
                return False

        if p.suffix in ['.template', '.disabled']:
            return False

        if config.RUNNING_TEST:
            suffixes = p.suffixes
            if len(suffixes) >= 1 and suffixes[-1] == '.bad': return False
            if len(suffixes) >= 2 and suffixes[-2] == '.bad': return False

        return True

    return sorted(p for p in path.glob(expression) if keep(p))


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
        if link.is_dir():
            shutil.rmtree(link)
        else:
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
        log(f'File "{inpath}" is not a text file.')
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
def copytree_and_substitute(src, dst, variables, exist_ok=True, *, preserve_symlinks=True):
    names = os.listdir(src)
    os.makedirs(dst, exist_ok=exist_ok)
    errors = []
    for name in names:
        try:
            srcFile = src / name
            dstFile = dst / name

            if preserve_symlinks and os.path.islink(srcFile):
                shutil.copy(srcFile, dstFile, follow_symlinks=False)
            elif (os.path.isdir(srcFile)):
                copytree_and_substitute(srcFile,
                                        dstFile,
                                        variables,
                                        exist_ok,
                                        preserve_symlinks=preserve_symlinks)
            elif (dstFile.exists()):
                warn(f'File "{dstFile}" already exists, skipping...')
                continue
            else:
                try:
                    data = srcFile.read_text()
                    data = substitute(data, variables)
                    dstFile.write_text(data)
                except UnicodeDecodeError:
                    # Do not substitute for binary files.
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
        output += cc.orange + 'Use -e to show more.' + cc.reset
    return output


# TODO: Move this to Problem.settings and read limits.memory variable from problem.yaml.
# Return memory limit in bytes.
def get_memory_limit(kwargs=None):
    memory_limit = 1024  # 1GB
    if hasattr(config.args, 'memory'):
        if config.args.memory:
            if config.args.memory != 'unlimited':
                memory_limit = int(config.args.memory)
            else:
                memory_limit = None  # disabled
    if kwargs and 'memory' in kwargs:
        memory_limit = kwargs['memory']
        kwargs.pop('memory')
    return memory_limit


class ExecResult:
    def __init__(self, ok, duration, err, out, verdict=None, print_verdict=None):
        self.ok = ok
        self.duration = duration
        self.err = err
        self.out = out
        self.verdict = verdict
        self.print_verdict_ = print_verdict

    def print_verdict(self):
        if self.print_verdict_: return self.print_verdict_
        return self.verdict


def limit_setter(command, timeout, memory_limit):
    def setlimits():
        if timeout:
            resource.setrlimit(resource.RLIMIT_CPU, (timeout + 1, timeout + 1))

        # Increase the max stack size from default to the max available.
        if sys.platform != 'darwin':
            resource.setrlimit(resource.RLIMIT_STACK,
                               (resource.RLIM_INFINITY, resource.RLIM_INFINITY))

        if memory_limit and not Path(command[0]).name in ['java', 'javac', 'kotlin', 'kotlinc']:
            resource.setrlimit(resource.RLIMIT_AS, (memory_limit*1024*1024, memory_limit*1024*1024))

        # Disable coredumps.
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return setlimits

# Subclass Popen to get rusage information.
class ResourcePopen(subprocess.Popen):
    # If wait4 is available, store resource usage information.
    if 'wait4' in dir(os):
        def _try_wait(self, wait_flags):
            """All callers to this function MUST hold self._waitpid_lock."""
            try:
                (pid, sts, res) = os.wait4(self.pid, wait_flags)
            except ChildProcessError:
                # This happens if SIGCLD is set to be ignored or waiting
                # for child processes has otherwise been disabled for our
                # process.  This child is dead, we can't get the status.
                pid = self.pid
                sts = 0
            else:
                self.rusage = res
            return (pid, sts)
    else:
        def _try_wait(self, wait_flags):
            """All callers to this function MUST hold self._waitpid_lock."""
            try:
                (pid, sts) = os.waitpid(self.pid, wait_flags)
            except ChildProcessError:
                # This happens if SIGCLD is set to be ignored or waiting
                # for child processes has otherwise been disabled for our
                # process.  This child is dead, we can't get the status.
                pid = self.pid
                sts = 0
            else:
                self.rusage = None
            return (pid, sts)

# Run `command`, returning stderr if the return code is unexpected.
def exec_command(command, expect=0, crop=True, **kwargs):
    # By default: discard stdout, return stderr
    if 'stdout' not in kwargs or kwargs['stdout'] is True: kwargs['stdout'] = subprocess.PIPE
    if 'stderr' not in kwargs or kwargs['stderr'] is True: kwargs['stderr'] = subprocess.PIPE

    # Convert any Pathlib objects to string.
    command = [str(x) for x in command]

    if config.args.verbose >= 2:
        if 'cwd' in kwargs: print('cd', kwargs['cwd'], '; ', end='')
        else: print('cd', Path.cwd(), '; ', end='')
        print(*command, end='')
        if 'stdin' in kwargs:
            print(' < ', kwargs['stdin'].name, end='')
        print()

    timeout = 30
    if 'timeout' in kwargs:
        if kwargs['timeout'] is None:
            timeout = None
        elif kwargs['timeout']:
            timeout = kwargs['timeout']
        kwargs.pop('timeout')

    process = None
    def interrupt_handler(sig, frame):
        nonlocal process
        process.kill()
        fatal('Running interrupted')

    if threading.current_thread() is threading.main_thread():
        old_handler = signal.signal(signal.SIGINT, interrupt_handler)

    did_timeout = False

    tstart = time.monotonic()
    try:
        if not is_windows():
            process = ResourcePopen(command,
                                       preexec_fn=limit_setter(command, timeout,
                                                               get_memory_limit(kwargs)),
                                       **kwargs)
        else:
            process = ResourcePopen(command, **kwargs)
        (stdout, stderr) = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Timeout expired.
        did_timeout = True
        process.kill()
        (stdout, stderr) = process.communicate()
    except PermissionError as e:
        # File is likely not executable.
        stdout = None
        stderr = str(e)
        return ExecResult(-1, 0, stderr, stdout)
    except OSError as e:
        # File probably doesn't exist.
        stdout = None
        stderr = str(e)
        return ExecResult(-1, 0, stderr, stdout)
    tend = time.monotonic()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, old_handler)

    # -2 corresponds to SIGINT, i.e. keyboard interrupt / CTRL-C.
    if process.returncode == -2:
        fatal('Child process interrupted.')

    def maybe_crop(s):
        return crop_output(s) if crop else s

    ok = True if process.returncode == expect else process.returncode
    err = maybe_crop(stderr.decode('utf-8')) if stderr is not None else None
    out = maybe_crop(stdout.decode('utf-8')) if stdout is not None else None

    if process.rusage:
        duration = process.rusage.ru_utime + process.rusage.ru_stime
        # It may happen that the Rusage is low, even though a timeout was raised, i.e. when calling sleep().
        # To prevent under-reporting the duration, we take the max with wall time in this case.
        if did_timeout:
            duration = max(tend-tstart, duration)
    else:
        duration = tend - tstart

    return ExecResult(ok, duration, err, out)
