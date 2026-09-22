# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Explicit helper upgrades distinguish the payload from its installer archive."""

from argparse import Namespace
from unittest.mock import Mock

import pytest

from pkm import cli


@pytest.fixture
def command(monkeypatch):
    db=Mock()
    db.root='/'
    db.get_installed.return_value={'name':'vscode','version':'1.0','release':6,
                                   'payload_version':'1:1.0-2'}
    db.list_installed.return_value=[db.get_installed.return_value]
    db.list_held.return_value=[]
    installer=Mock()
    installer.query_helper_version.return_value=({'version':'1:1.0-10','comparison':1},'')
    repo=Mock()
    repo.get_package.return_value=None
    run=Mock(return_value='ok')
    messages=[]
    for name in ['emit_info','emit_error','emit_warn']:
        monkeypatch.setattr(cli,name,lambda message:messages.append(message))
    monkeypatch.setattr(cli,'package_installer',lambda db:installer)
    monkeypatch.setattr(cli,'repo_manager',lambda:repo)
    monkeypatch.setattr(cli,'_proprietary_install',run)
    monkeypatch.setattr(cli,'helper_is_present',lambda name:True)
    monkeypatch.setattr('pkm.pretxn.run_pre_transaction_hook',lambda *a,**k:None)
    args=Namespace(packages=['vscode'],upgrade_all=False,upgrade_yes=True,
                   upgrade_dry_run=False,ignore_holds=False,quiet=False,verbose=False)
    return db,installer,repo,run,messages,args


@pytest.mark.parametrize('comparison',[-1,0])
def test_equal_or_local_newer_reports_both_versions_without_install(command,comparison):
    db,installer,repo,run,messages,args=command
    installer.query_helper_version.return_value=({'version':'1:1.0-2','comparison':comparison},'')
    assert cli.cmd_upgrade(db,args)==0
    installer.query_helper_version.assert_called_once_with('vscode','1:1.0-2')
    run.assert_not_called()
    assert any('installed' in m and 'vendor' in m and '1:1.0-2' in m for m in messages)
    repo.download_package.assert_not_called()


def test_newer_payload_uses_the_verified_install_path_with_exact_version(command):
    db,installer,repo,run,messages,args=command
    assert cli.cmd_upgrade(db,args)==0
    assert run.call_args.kwargs=={'replace':True,'target_version':'1:1.0-10'}
    repo.download_package.assert_not_called()


def test_metadata_failure_is_an_error_and_never_up_to_date(command):
    db,installer,repo,run,messages,args=command
    installer.query_helper_version.return_value=(None,'vendor metadata could not be verified')
    with pytest.raises(SystemExit) as exc:cli.cmd_upgrade(db,args)
    assert exc.value.code==1
    run.assert_not_called()
    assert not any('up to date' in m.lower() for m in messages)


def test_dry_run_queries_but_does_not_install(command):
    db,installer,repo,run,messages,args=command
    args.upgrade_dry_run=True
    assert cli.cmd_upgrade(db,args)==0
    installer.query_helper_version.assert_called_once()
    run.assert_not_called()


def test_hold_refuses_before_querying_vendor(command):
    db,installer,repo,run,messages,args=command
    db.list_held.return_value=['vscode']
    with pytest.raises(SystemExit) as exc:cli.cmd_upgrade(db,args)
    assert exc.value.code==1
    installer.query_helper_version.assert_not_called()
    run.assert_not_called()


def test_missing_recorded_payload_is_not_a_current_version(command):
    db,installer,repo,run,messages,args=command
    db.get_installed.return_value['payload_version']=None
    with pytest.raises(SystemExit) as exc:cli.cmd_upgrade(db,args)
    assert exc.value.code==1
    installer.query_helper_version.assert_not_called()
    run.assert_not_called()


def test_reinstall_requests_the_recorded_payload_instead_of_latest(command):
    db,installer,repo,run,messages,args=command
    cli.cmd_reinstall(db,args)
    assert run.call_args.kwargs=={'replace':True,'target_version':'1:1.0-2'}
    installer.query_helper_version.assert_not_called()


def test_every_vendor_is_checked_before_any_payload_is_replaced(command):
    db,installer,repo,run,messages,args=command
    args.packages=['vscode','chrome']
    installer.query_helper_version.side_effect=[({'version':'2.0','comparison':1},''),(None,'unreachable')]
    with pytest.raises(SystemExit) as exc:cli.cmd_upgrade(db,args)
    assert exc.value.code==1
    run.assert_not_called()


def test_all_keeps_the_repository_archive_plan(command,monkeypatch):
    db,installer,repo,run,messages,args=command
    args.packages=[]
    args.upgrade_all=True
    candidates=Mock(return_value=([],[]))
    monkeypatch.setattr(cli,'_repository_upgrade_candidates',candidates)
    assert cli.cmd_upgrade(db,args)==0
    candidates.assert_called_once()
    installer.query_helper_version.assert_not_called()
    run.assert_not_called()


def test_mixed_request_keeps_the_other_package_without_changing_arguments(command,monkeypatch):
    db,installer,repo,run,messages,args=command
    args.packages=['vscode','bash']
    candidates=Mock(return_value=([],[]))
    monkeypatch.setattr(cli,'_repository_upgrade_candidates',candidates)
    assert cli.cmd_upgrade(db,args)==0
    assert candidates.call_args.args[2].packages==['bash']
    assert args.packages==['vscode','bash']
    run.assert_called_once()


def test_failed_payload_install_exits_nonzero(command):
    db,installer,repo,run,messages,args=command
    run.return_value='failed'
    with pytest.raises(SystemExit) as exc:cli.cmd_upgrade(db,args)
    assert exc.value.code==1


def test_an_older_helper_is_never_executed_to_discover_its_api(tmp_path):
    from pkm.installer import PackageInstaller
    marker=tmp_path/'ran'
    helper=tmp_path/'old-helper'
    helper.write_text('#!/bin/sh\ntouch '+str(marker)+'\n')
    helper.chmod(0o755)
    installer=PackageInstaller.__new__(PackageInstaller)
    installer._find_helper=lambda name:helper
    result,error=installer.query_helper_version('vscode','1.0')
    assert result is None and 'updated installer' in error
    assert not marker.exists()


@pytest.mark.parametrize('reply',['{}','{"version":"2.0","comparison":true}',
                                '{"version":"2.0","comparison":2}','not-json'])
def test_invalid_helper_reply_is_refused(tmp_path,reply):
    import shlex
    from pkm.installer import PackageInstaller
    helper=tmp_path/'helper'
    helper.write_text('#!/bin/sh\n# pkm-apt-helper-api: 1\nprintf "%s\\n" '+shlex.quote(reply)+'\n')
    helper.chmod(0o755)
    installer=PackageInstaller.__new__(PackageInstaller)
    installer._find_helper=lambda name:helper
    result,error=installer.query_helper_version('vscode','1.0')
    assert result is None and error
