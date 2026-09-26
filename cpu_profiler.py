#!/usr/bin/env python3
"""Timestamped machine-wide measurements. Requires psutil. Stop with Ctrl+C."""
import argparse
import csv
from datetime import datetime, timezone
import math
from pathlib import Path
import signal
import socket
import time

try:
    import psutil
except ImportError:
    raise SystemExit('Missing psutil. Install it with: python3 -m pip install psutil')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', default='cpu.csv')
    p.add_argument('--label', default=socket.gethostname(), help='e.g. proxy or host')
    p.add_argument('--interval', type=float, default=1.0)
    p.add_argument('--duration', type=float, default=0, help='0 means until Ctrl+C')
    p.add_argument('--overwrite', action='store_true')
    a = p.parse_args()
    if not math.isfinite(a.interval) or a.interval <= 0:
        p.error('--interval must be finite and positive')
    if not math.isfinite(a.duration) or a.duration < 0:
        p.error('--duration must be finite and nonnegative')
    path = Path(a.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    cores = len(psutil.cpu_percent(percpu=True))
    fields = ['timestamp_utc', 'sample_start_epoch', 'epoch', 'elapsed_s', 'label',
              'hostname', 'cpu_percent', 'cpu_peak_core_percent', 'memory_percent',
              'memory_used_mib', 'memory_available_mib', 'swap_percent',
              'net_sent_kib_s', 'net_recv_kib_s', 'net_errors_delta', 'net_drops_delta']
    fields += ['cpu_core_%d_percent' % i for i in range(cores)]
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    psutil.cpu_percent(None)
    psutil.cpu_percent(None, percpu=True)
    previous_net = psutil.net_io_counters()
    start = previous_mono = time.monotonic()
    previous_epoch = time.time()
    try:
        f = path.open('w' if a.overwrite else 'x', newline='')
    except FileExistsError:
        p.error('Output exists. Use a new filename or explicitly pass --overwrite.')
    print('Recording %s: UTC timestamps, %d logical CPUs. Ctrl+C to stop.' % (a.label, cores), flush=True)
    with f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        f.flush()
        try:
            while True:
                remaining = a.duration - (time.monotonic() - start) if a.duration else a.interval
                if remaining <= 0:
                    break
                time.sleep(min(a.interval, remaining))
                cpu = psutil.cpu_percent(None)
                percore = psutil.cpu_percent(None, percpu=True)
                now_mono, now_epoch = time.monotonic(), time.time()
                dt = now_mono - previous_mono
                mem, swap, net = psutil.virtual_memory(), psutil.swap_memory(), psutil.net_io_counters()
                stamp = datetime.fromtimestamp(now_epoch, timezone.utc).isoformat(timespec='milliseconds')
                row = dict(timestamp_utc=stamp, sample_start_epoch=round(previous_epoch, 6),
                           epoch=round(now_epoch, 6), elapsed_s=round(now_mono-start, 3),
                           label=a.label, hostname=socket.gethostname(), cpu_percent=cpu,
                           cpu_peak_core_percent=max(percore, default=0), memory_percent=mem.percent,
                           memory_used_mib=round(mem.used/2**20, 2), memory_available_mib=round(mem.available/2**20, 2),
                           swap_percent=swap.percent,
                           net_sent_kib_s=round(max(0, net.bytes_sent-previous_net.bytes_sent)/dt/1024, 2),
                           net_recv_kib_s=round(max(0, net.bytes_recv-previous_net.bytes_recv)/dt/1024, 2),
                           net_errors_delta=max(0, net.errin-previous_net.errin)+max(0, net.errout-previous_net.errout),
                           net_drops_delta=max(0, net.dropin-previous_net.dropin)+max(0, net.dropout-previous_net.dropout))
                row.update({'cpu_core_%d_percent'%i: percore[i] if i < len(percore) else '' for i in range(cores)})
                w.writerow(row)
                f.flush()
                print('%s [%s] CPU %5.1f%% | busiest core %5.1f%% | RAM %5.1f%% | TX %.1f / RX %.1f KiB/s' %
                      (stamp, a.label, cpu, row['cpu_peak_core_percent'], mem.percent, row['net_sent_kib_s'], row['net_recv_kib_s']), flush=True)
                previous_net, previous_mono, previous_epoch = net, now_mono, now_epoch
        except KeyboardInterrupt:
            pass
    print('Saved: %s' % path)


if __name__ == '__main__':
    main()
