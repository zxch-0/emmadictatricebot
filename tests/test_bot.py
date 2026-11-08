import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.update(DISCORD_TOKEN='test', GUILD_ID='123', PANEL_ROLE_ID='456', LOG_CHANNEL_ID='', PUNISHMENT_ROLE_ID='789')
import bot


class Role:
    def __init__(self, id, position, permissions=0, managed=False):
        self.id, self.position, self.managed = id, position, managed
        self.permissions = SimpleNamespace(value=permissions)

    def __ge__(self, other):
        return self.position >= other.position


class BotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.actor = SimpleNamespace(id=1, top_role=Role(10, 8), mention='<@1>')
        self.me = SimpleNamespace(top_role=Role(11, 10), guild_permissions=SimpleNamespace(**{p: True for p in bot.PERMISSIONS.values() if p}))
        self.member = SimpleNamespace(id=2, top_role=Role(12, 1), roles=[], voice=SimpleNamespace(channel=object()),
                                      kick=AsyncMock(), ban=AsyncMock(), timeout=AsyncMock(), edit=AsyncMock(), add_roles=AsyncMock(), remove_roles=AsyncMock())
        self.guild = SimpleNamespace(id=123, owner_id=99, me=self.me, channels=[], fetch_member=AsyncMock(return_value=self.member),
                                     unban=AsyncMock(), get_channel=MagicMock(), get_role=MagicMock(return_value=Role(789, 2)))
        self.interaction = SimpleNamespace(guild=self.guild, user=self.actor,
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        bot.bot._connection.user = SimpleNamespace(id=999)

    async def submit(self, kind, target='2', reason='Motif', extra=None, permitted=True):
        modal = bot.SanctionModal({'title': 'Test', 'kind': kind})
        modal.target_id._value = target
        modal.reason._value = reason
        if kind in ('timeout', 'reminder_start'): modal.duration._value = extra or '60'
        if kind == 'nickname': modal.nickname._value = extra or 'Patate royale'
        with patch.object(bot, 'allowed', return_value=permitted):
            await modal.on_submit(self.interaction)

    def test_panel_role_configuration(self):
        self.assertEqual(bot.parse_panel_roles('456', 123), {456})
        self.assertEqual(bot.parse_panel_roles('456, 789', 123), {456, 789})
        for value in ('', '456,', ',456', '456,abc', '456,789,111', '0', '456,123', '-1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bot.parse_panel_roles(value, 123)

    async def test_either_role_grants_access_and_is_protected(self):
        with patch.object(bot, 'ROLE_IDS', {456, 789}), patch.object(bot.discord, 'Member', SimpleNamespace):
            for role_id in (456, 789):
                self.interaction.user.roles = [Role(role_id, 1)]
                self.assertTrue(bot.allowed(self.interaction))
                self.member.roles = [Role(role_id, 1)]
                await self.submit('kick')
                self.member.kick.assert_not_awaited()
                self.assertFalse(bot.safe_punishment_role(self.guild, Role(role_id, 2), self.me, self.actor))
            self.interaction.user.roles = [Role(111, 1)]
            self.assertFalse(bot.allowed(self.interaction))

    async def test_reminders_stop_for_second_authorized_role(self):
        bot.REMINDERS.clear()
        bot.LAST_REMINDER_SENT.clear()
        await self.submit('reminder_start', extra='1')
        author = SimpleNamespace(id=2, bot=False, roles=[Role(789, 1)], send=AsyncMock())
        with patch.object(bot, 'ROLE_IDS', {456, 789}):
            await bot.on_message(SimpleNamespace(guild=self.guild, author=author))
        self.assertNotIn(2, bot.REMINDERS)
        author.send.assert_not_awaited()

    async def test_all_actions(self):
        for kind in bot.PERMISSIONS:
            if kind in ('reminder_start', 'reminder_stop'): continue
            with self.subTest(kind=kind):
                self.setUp()
                await self.submit(kind)
                self.interaction.followup.send.assert_awaited_once()
                self.assertIn('✅', self.interaction.followup.send.call_args.args[0])
                method = {'kick':'kick', 'ban':'ban', 'timeout':'timeout', 'untimeout':'timeout',
                          'nickname':'edit', 'reset_nickname':'edit', 'role_add':'add_roles', 'role_remove':'remove_roles',
                          'voice_mute':'edit', 'voice_unmute':'edit', 'voice_deafen':'edit', 'voice_undeafen':'edit', 'voice_disconnect':'edit'}.get(kind)
                (getattr(self.member, method) if method else self.guild.unban).assert_awaited_once()

    async def test_rejects_unauthorized_and_invalid_input(self):
        await self.submit('kick', permitted=False)
        self.interaction.response.send_message.assert_awaited_once()
        self.guild.fetch_member.assert_not_awaited()
        self.setUp()
        await self.submit('timeout', extra='40321')
        self.interaction.response.send_message.assert_awaited_once()
        self.guild.fetch_member.assert_not_awaited()
        self.setUp()
        await self.submit('kick', target='non')
        self.guild.fetch_member.assert_not_awaited()

    async def test_protected_member_and_permission(self):
        self.member.roles = [Role(456, 1)]
        await self.submit('ban')
        self.member.ban.assert_not_awaited()
        self.setUp()
        self.me.guild_permissions.kick_members = False
        await self.submit('kick')
        self.guild.fetch_member.assert_not_awaited()

    async def test_unsafe_role_and_absent_voice(self):
        self.guild.get_role.return_value = Role(789, 2, permissions=8)
        await self.submit('role_add')
        self.member.add_roles.assert_not_awaited()
        self.setUp()
        self.member.voice = None
        await self.submit('voice_disconnect')
        self.member.edit.assert_not_awaited()

    async def test_category_components_and_commands(self):
        for category, (_, actions) in bot.CATEGORIES.items():
            view = bot.SanctionsView(category)
            self.assertEqual(len(view.children[0].options), len(actions))
            for title, kind, _ in actions:
                self.assertIn(kind, bot.PERMISSIONS)
                bot.SanctionModal({'title': title, 'kind': kind})
        self.assertEqual(len(bot.bot.tree.get_commands(guild=bot.discord.Object(id=123))), 1)

    async def test_reminder_rate_limit_stop_and_expiry(self):
        bot.REMINDERS.clear()
        bot.LAST_REMINDER_SENT.clear()
        await self.submit('reminder_start', extra='5')
        self.assertIn(2, bot.REMINDERS)
        author = SimpleNamespace(id=2, bot=False, roles=[], send=AsyncMock())
        msg = SimpleNamespace(guild=self.guild, author=author)
        with patch.object(bot.time, 'monotonic', return_value=bot.REMINDERS[2]['expires'] - 100):
            await bot.on_message(msg)
            await bot.on_message(msg)
        author.send.assert_awaited_once()
        dm = SimpleNamespace(guild=None, author=author, content='STOP', channel=SimpleNamespace(send=AsyncMock()))
        await bot.on_message(dm)
        self.assertNotIn(2, bot.REMINDERS)
        dm.channel.send.assert_awaited_once()
        await self.submit('reminder_start', extra='1')
        with patch.object(bot.time, 'monotonic', return_value=bot.REMINDERS[2]['expires']):
            await bot.on_message(msg)
        self.assertNotIn(2, bot.REMINDERS)
        author.send.assert_awaited_once()

    async def test_reminder_removed_when_recipient_becomes_protected(self):
        bot.REMINDERS.clear()
        bot.LAST_REMINDER_SENT.clear()
        await self.submit('reminder_start', extra='1')
        author = SimpleNamespace(id=2, bot=False, roles=[Role(456, 1)], send=AsyncMock())
        await bot.on_message(SimpleNamespace(guild=self.guild, author=author))
        self.assertNotIn(2, bot.REMINDERS)
        author.send.assert_not_awaited()

    def test_role_channel_permissions(self):
        role = Role(789, 2)
        self.guild.channels = [SimpleNamespace(overwrites_for=lambda _: SimpleNamespace(pair=lambda: (SimpleNamespace(value=1024), None)))]
        self.assertFalse(bot.safe_punishment_role(self.guild, role, self.me, self.actor))


if __name__ == '__main__':
    unittest.main()
