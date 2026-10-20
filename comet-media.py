#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Attach a previously uploaded .img on a GL.iNet Comet, or detach its media.

Uses the stock 1.10.1 release 2 API exercised during bring-up. Credentials are
prompted for, or read from stdin with --password-stdin; never pass them in argv.
"""
import argparse
import getpass
import sys
from urllib.parse import urlsplit

import requests
import urllib3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', help='existing filename in /userdata/media, or detach')
    parser.add_argument('--url', required=True, help='Comet HTTPS origin, e.g. https://comet.example')
    parser.add_argument('--user', default='admin')
    parser.add_argument('--password-stdin', action='store_true')
    parser.add_argument('--insecure', action='store_true', help='accept the appliance self-signed certificate')
    args = parser.parse_args()
    if args.image != 'detach' and ('/' in args.image or '\\' in args.image or not args.image.endswith('.img')):
        parser.error('expected an .img filename without directory components, or detach')
    base = args.url.rstrip('/')
    url = urlsplit(base)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.path or url.query or url.fragment):
        parser.error('--url must be an HTTPS origin without a path or credentials')
    password = sys.stdin.readline().rstrip('\r\n') if args.password_stdin else getpass.getpass('Comet password: ')
    if not password:
        parser.error('password is empty')
    if args.insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    with requests.Session() as session:
        session.verify = not args.insecure
        response = session.post(base + '/api/auth/login', data={'user': args.user, 'passwd': password}, timeout=20)
        response.raise_for_status()
        session.headers.update({'token': session.cookies.get('auth_token'), 'Origin': base, 'Referer': base + '/'})

        def call(path, params):
            response = session.post(base + path, params=params, timeout=30)
            response.raise_for_status()
            if not response.json().get('ok'):
                raise RuntimeError('Comet API operation failed: ' + path)
            print(path, 'OK')

        response = session.get(base + '/api/msd', timeout=20)
        response.raise_for_status()
        if response.json()['result']['drive']['connected']:
            call('/api/msd/set_connected', {'connected': '0'})
        if args.image != 'detach':
            call('/api/msd/set_params', {'image': args.image, 'cdrom': '0', 'rw': '1'})
            call('/api/msd/set_connected', {'connected': '1'})
        response = session.get(base + '/api/msd', timeout=20)
        response.raise_for_status()
        print(response.json()['result']['drive'])


if __name__ == '__main__':
    main()
