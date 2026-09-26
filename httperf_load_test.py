#!/usr/bin/env python3
"""Mac/Linux httperf runner. Python standard library only; httperf required.
One GET per TCP connection. Default target: the user's local Nginx lab.
Results are measurements of the complete client/network/proxy/backend system.
"""
import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import re
import resource
import shlex
import shutil
import subprocess
import time


def number(text, pattern):
    match = re.search(pattern, text, re.MULTILINE)
    return float(match.group(1)) if match else None


def parse(text):
    patterns = {
        'requests': r'^Total: connections \d+ requests (\d+)',
        'replies': r'^Total: connections \d+ requests \d+ replies (\d+)',
        'duration_s': r'^Total:.*test-duration ([\d.]+)',
        'sent_req_s': r'^Request rate: ([\d.]+)',
        'response_ms': r'^Reply time \[ms\]: response ([\d.]+)',
        'transfer_ms': r'^Reply time \[ms\]:.*transfer ([\d.]+)',
        'connection_avg_ms': r'^Connection time \[ms\]: min [\d.]+ avg ([\d.]+)',
        'http_2xx': r'^Reply status:.*2xx=(\d+)',
        'http_3xx': r'^Reply status:.*3xx=(\d+)',
        'http_4xx': r'^Reply status:.*4xx=(\d+)',
        'http_5xx': r'^Reply status:.*5xx=(\d+)',
        'errors': r'^Errors: total (\d+)',
        'client_timeouts': r'^Errors:.*client-timo (\d+)',
        'socket_timeouts': r'^Errors:.*socket-timo (\d+)',
        'refused': r'^Errors:.*connrefused (\d+)',
        'reset': r'^Errors:.*connreset (\d+)',
        'fd_unavailable': r'^Errors:.*fd-unavail (\d+)',
        'address_unavailable': r'^Errors:.*addrunavail (\d+)',
        'file_table_full': r'^Errors:.*ftab-full (\d+)',
        'generator_cpu_pct': r'^CPU time.*total ([\d.]+)%',
    }
    values = {key: number(text, pat) for key, pat in patterns.items()}
    duration = values['duration_s']
    for name, numerator in [('replies_s', 'replies'), ('successful_req_s', 'http_2xx')]:
        values[name] = (round(values[numerator] / duration, 3)
                        if duration and values[numerator] is not None else None)
    return values


def probe(host, port, uri):
    """curl has a hard overall deadline and bypasses environment proxies."""
    result = subprocess.run(
        ['curl', '--noproxy', '*', '-sS', '--connect-timeout', '2',
         '--max-time', '5', '-w', '\n%{http_code}',
         'http://{}:{}{}'.format(host, port, uri)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    body, _, code = result.stdout.rpartition('\n')
    identity = body[:180]
    try:
        data = json.loads(body)
        identity = '{} / {}'.format(data.get('hostname'), data.get('ip'))
    except (ValueError, AttributeError):
        pass
    return code if result.returncode == 0 else 'curl_error', identity


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', default='192.168.98.12')
    p.add_argument('--port', type=int, default=80)
    p.add_argument('--uri', default='/api/solve')
    p.add_argument('--rates', default='50,100,200,400,500,750,1000,1500,2000')
    p.add_argument('--seconds', type=int, default=20, help='nominal load period per rate')
    p.add_argument('--timeout', type=int, default=5, help='httperf inactivity timeout')
    p.add_argument('--cooldown', type=int, default=20)
    p.add_argument('--repeats', type=int, default=1)
    args = p.parse_args()
    try:
        rates = [int(x) for x in args.rates.split(',')]
        assert rates and min(rates) > 0
        assert min(args.seconds, args.timeout, args.repeats) > 0
        assert args.cooldown >= 0 and 1 <= args.port <= 65535
        assert args.uri.startswith('/')
    except (ValueError, AssertionError):
        p.error('Use positive rates/durations/repeats, a valid port and a URI starting with /.')
    for tool in ('httperf', 'curl'):
        if not shutil.which(tool):
            p.error('{} is missing. Install httperf with: brew install httperf'.format(tool))
    # Raise only this process's soft limit, within its existing hard limit.
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = max(soft, 4096) if hard == resource.RLIM_INFINITY else min(hard, max(soft, 4096))
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (ValueError, OSError):
        pass
    out = Path('results_' + dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    out.mkdir()
    env = dict(os.environ, LC_ALL='C')
    metadata = dict(vars(args), fd_limits=resource.getrlimit(resource.RLIMIT_NOFILE),
                    note='Missing CSV values are unavailable, not zero. CPU is httperf CPU, not VM CPU.')
    (out / 'settings.json').write_text(json.dumps(metadata, indent=2))
    print('Saving results to:', out.resolve(), flush=True)
    with (out / 'preflight.txt').open('w') as f:
        for _ in range(6):
            code, identity = probe(args.host, args.port, args.uri)
            line = '{} {}'.format(code, identity)
            print('Preflight:', line, flush=True)
            f.write(line + '\n')
    if code != '200':
        print('Preflight failed. Fix connectivity/application errors before load testing.')
        return 1
    fields = ['time', 'host', 'rate_target', 'planned_requests', 'repeat', 'status',
              'exit_code', 'wall_s'] + list(parse('').keys()) + ['success_pct_planned']
    with (out / 'benchmark_results.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for rate in rates:
            for repeat in range(1, args.repeats + 1):
                count = rate * args.seconds
                cmd = ['httperf', '--server=' + args.host, '--port=' + str(args.port),
                       '--uri=' + args.uri, '--num-conns=' + str(count), '--num-calls=1',
                       '--rate=' + str(rate), '--timeout=' + str(args.timeout)]
                stem = '{}_r{}'.format(rate, repeat)
                (out / ('command_' + stem + '.txt')).write_text(shlex.join(cmd) + '\n')
                log = out / ('httperf_' + stem + '.txt')
                deadline = args.seconds + 4 * args.timeout + 15
                print('Testing {} req/s, repeat {} (hard deadline {}s)...'.format(rate, repeat, deadline), flush=True)
                started = time.monotonic()
                stamp = dt.datetime.now().astimezone().isoformat()
                with log.open('w') as raw:
                    proc = subprocess.Popen(cmd, stdout=raw, stderr=subprocess.STDOUT, env=env)
                    status = 'completed'
                    try:
                        proc.wait(timeout=deadline)
                    except subprocess.TimeoutExpired:
                        status = 'watchdog_timeout'
                        proc.terminate()
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                    except KeyboardInterrupt:
                        proc.kill()
                        proc.wait()
                        print('\nStopped. Earlier results and current raw log are saved.')
                        return 130
                wall = round(time.monotonic() - started, 3)
                stats = parse(log.read_text(errors='replace'))
                if status == 'completed':
                    if proc.returncode != 0:
                        status = 'process_error'
                    elif any(stats[k] is None for k in ('duration_s', 'replies', 'http_2xx', 'errors')):
                        status = 'incomplete_summary'
                    elif stats['http_2xx'] < count or stats['errors'] > 0:
                        status = 'completed_with_failures'
                # A terminated run may have no summary. Do not manufacture measurements.
                pct = round(100 * stats['http_2xx'] / count, 2) if stats['http_2xx'] is not None else None
                print('  {} | successful req/s={} | response ms={} | success={}%' .format(
                    status, stats['successful_req_s'], stats['response_ms'], pct), flush=True)
                row = dict(time=stamp, host=args.host, rate_target=rate, planned_requests=count,
                           repeat=repeat, status=status, exit_code=proc.returncode, wall_s=wall,
                           success_pct_planned=pct, **stats)
                writer.writerow(row)
                f.flush()
                print('  Cooling down for {}s; continuing to next test even after errors.'.format(args.cooldown), flush=True)
                time.sleep(args.cooldown)
                recovery, identity = probe(args.host, args.port, args.uri)
                with (out / 'recovery.txt').open('a') as recovery_log:
                    recovery_log.write('{} {} {}\n'.format(stem, recovery, identity))
                print('  Recovery probe:', recovery, identity, flush=True)
    print('Done:', (out / 'benchmark_results.csv').resolve())
    print('Check raw logs for generator limits before attributing failures to Nginx.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('\nStopped; completed rows remain saved.')
        raise SystemExit(130)
