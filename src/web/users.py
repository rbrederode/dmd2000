"""Manage the shared console user database without placing passwords on the command line."""

import argparse
from getpass import getpass

from flask import Flask
from web.security import init_security, save_user, User, db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'update', 'list'])
    parser.add_argument('--username')
    parser.add_argument('--role', choices=['viewer', 'operator', 'admin'])
    parser.add_argument('--state-dir', help='Shared WEB UI state directory (same as --ui_state_dir)')
    parser.add_argument('--password', action='store_true', help='Prompt to change an existing password')
    parser.add_argument('--disable', action='store_true')
    parser.add_argument('--enable', action='store_true')
    args = parser.parse_args()
    app = Flask('dmd2000-users')
    security = init_security(app, args.state_dir)
    with app.app_context():
        if args.action == 'list':
            for user in User.query.order_by(User.username):
                print(user.username, 'active' if user.active else 'disabled', ','.join(role.name for role in user.roles))
            return
        if not args.username:
            parser.error('--username is required')
        user = security.datastore.find_user(username=args.username.lower())
        if args.action == 'create' and user:
            parser.error('Username already exists')
        if args.action == 'update' and user is None:
            parser.error('User was not found')
        if args.disable and args.enable:
            parser.error('Choose --disable or --enable')
        changes = {'username': args.username}
        if args.role:
            changes['role'] = args.role
        if args.disable or args.enable:
            changes['active'] = args.enable
        if args.action == 'create' or args.password:
            password = getpass('Password (at least 12 characters): ')
            if password != getpass('Confirm password: '):
                parser.error('Passwords do not match')
            changes['password'] = password
        try:
            user = save_user(security.datastore, changes, user)
        except ValueError as error:
            parser.error(str(error))
        print('Saved user', user.username)


if __name__ == '__main__':
    main()
