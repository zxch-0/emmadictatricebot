import os
import logging
import time
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ['DISCORD_TOKEN']
GUILD_ID = int(os.environ['GUILD_ID'])
def parse_panel_roles(value, guild_id):
    parts = [part.strip() for part in value.split(',')]
    if not 1 <= len(parts) <= 2 or any(not part.isascii() or not part.isdecimal() for part in parts):
        raise ValueError('PANEL_ROLE_ID doit contenir un ou deux IDs numériques séparés par une virgule.')
    roles = {int(part) for part in parts}
    if 0 in roles or guild_id in roles:
        raise ValueError('PANEL_ROLE_ID doit contenir des IDs positifs et ne peut pas inclure @everyone.')
    return roles


ROLE_IDS = parse_panel_roles(os.environ['PANEL_ROLE_ID'], GUILD_ID)
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID') or '0')
PUNISHMENT_ROLE_ID = int(os.getenv('PUNISHMENT_ROLE_ID') or '0')
# Mémoire uniquement : remise à zéro au redémarrage du worker.
REMINDERS = {}
LAST_REMINDER_SENT = {}
REMINDER_INTERVAL = 60
MAX_REMINDERS = 100

intents = discord.Intents.default()
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)


def allowed(interaction):
    return (interaction.guild is not None and interaction.guild.id == GUILD_ID
            and isinstance(interaction.user, discord.Member)
            and any(role.id in ROLE_IDS for role in interaction.user.roles))


async def deny(interaction):
    await interaction.response.send_message('Accès refusé : ce panel est réservé au rôle autorisé.', ephemeral=True)


class GuardedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=900)

    async def interaction_check(self, interaction):
        if not allowed(interaction):
            await deny(interaction)
            return False
        return True


class SanctionModal(discord.ui.Modal):
    def __init__(self, action):
        super().__init__(title=action['title'])
        self.action = action
        self.target_id = discord.ui.TextInput(label='ID du membre', placeholder='Ex. 123456789012345678', max_length=20)
        self.reason = discord.ui.TextInput(label='Motif', style=discord.TextStyle.paragraph, max_length=400)
        self.add_item(self.target_id)
        self.add_item(self.reason)
        if action['kind'] in ('timeout', 'reminder_start'):
            limit = 40320 if action['kind'] == 'timeout' else 1440
            self.duration = discord.ui.TextInput(label=f'Durée en minutes (1 à {limit})', placeholder='Ex. 60', max_length=5)
            self.add_item(self.duration)
        if action['kind'] == 'nickname':
            self.nickname = discord.ui.TextInput(label='Nouveau surnom', placeholder='Ex. Roi des chaussettes', max_length=32)
            self.add_item(self.nickname)

    async def on_submit(self, interaction):
        if not allowed(interaction):
            return await deny(interaction)
        try:
            target_id = int(str(self.target_id).strip())
            if target_id <= 0:
                raise ValueError
            minutes = None
            if self.action['kind'] in ('timeout', 'reminder_start'):
                minutes = int(str(self.duration).strip())
                limit = 40320 if self.action['kind'] == 'timeout' else 1440
                if not 1 <= minutes <= limit:
                    raise ValueError
        except ValueError:
            return await interaction.response.send_message('ID ou durée invalide. Vérifie la limite de durée indiquée dans le formulaire.', ephemeral=True)
        reason = str(self.reason).strip()
        if not reason:
            return await interaction.response.send_message('Le motif ne peut pas être vide.', ephemeral=True)
        nickname = str(self.nickname).strip() if self.action['kind'] == 'nickname' else None
        if self.action['kind'] == 'nickname' and not nickname:
            return await interaction.response.send_message('Le surnom ne peut pas être vide.', ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        kind = self.action['kind']
        try:
            me = guild.me or await guild.fetch_member(bot.user.id)
            permission = PERMISSIONS[kind]
            if permission and not getattr(me.guild_permissions, permission):
                return await interaction.followup.send(f'Il manque au bot la permission Discord `{permission}`.', ephemeral=True)
            if kind == 'unban':
                await guild.unban(discord.Object(id=target_id), reason=f'{interaction.user} : {reason}')
            else:
                member = await guild.fetch_member(target_id)
                actor = interaction.user
                if (member.id in (guild.owner_id, actor.id, bot.user.id)
                        or any(role.id in ROLE_IDS for role in member.roles)
                        or member.top_role >= me.top_role
                        or (actor.id != guild.owner_id and member.top_role >= actor.top_role)):
                    return await interaction.followup.send('Action impossible : cible protégée ou hiérarchie des rôles insuffisante.', ephemeral=True)
                audit_reason = f'{actor} : {reason}'
                if kind == 'kick':
                    await member.kick(reason=audit_reason)
                elif kind == 'ban':
                    await member.ban(reason=audit_reason, delete_message_seconds=0)
                elif kind == 'timeout':
                    await member.timeout(timedelta(minutes=minutes), reason=audit_reason)
                elif kind == 'untimeout':
                    await member.timeout(None, reason=audit_reason)
                elif kind == 'reminder_start':
                    now = time.monotonic()
                    # Purger les rappels expirés avant de vérifier la limite.
                    for key, reminder in list(REMINDERS.items()):
                        if reminder['expires'] <= now:
                            REMINDERS.pop(key, None)
                    if member.id not in REMINDERS and len(REMINDERS) >= MAX_REMINDERS:
                        return await interaction.followup.send('La limite de rappels actifs est atteinte.', ephemeral=True)
                    REMINDERS[member.id] = {'expires': now + minutes * 60, 'next_send': 0}
                elif kind == 'reminder_stop':
                    if REMINDERS.pop(member.id, None) is None:
                        return await interaction.followup.send('Aucun rappel actif pour ce membre.', ephemeral=True)
                elif kind in ('nickname', 'reset_nickname'):
                    await member.edit(nick=nickname if kind == 'nickname' else None, reason=audit_reason)
                elif kind in ('role_add', 'role_remove'):
                    role = guild.get_role(PUNISHMENT_ROLE_ID) if PUNISHMENT_ROLE_ID else None
                    if not safe_punishment_role(guild, role, me, actor):
                        return await interaction.followup.send('Rôle de punition absent ou non sûr : vérifie sa configuration et sa hiérarchie.', ephemeral=True)
                    if kind == 'role_add':
                        await member.add_roles(role, reason=audit_reason)
                    else:
                        await member.remove_roles(role, reason=audit_reason)
                elif kind in ('voice_mute', 'voice_unmute', 'voice_deafen', 'voice_undeafen', 'voice_disconnect'):
                    if not member.voice or not member.voice.channel:
                        return await interaction.followup.send('Ce membre doit être connecté à un salon vocal.', ephemeral=True)
                    changes = {
                        'voice_mute': {'mute': True}, 'voice_unmute': {'mute': False},
                        'voice_deafen': {'deafen': True}, 'voice_undeafen': {'deafen': False},
                        'voice_disconnect': {'voice_channel': None},
                    }
                    await member.edit(reason=audit_reason, **changes[kind])
            await interaction.followup.send(f'✅ {self.action["title"]} effectué pour `{target_id}`.', ephemeral=True)
            if LOG_CHANNEL_ID:
                channel = guild.get_channel(LOG_CHANNEL_ID)
                if channel:
                    try:
                        await channel.send(f'🛡️ **{self.action["title"]}** · cible `{target_id}` · modérateur {interaction.user.mention}\nMotif : {discord.utils.escape_markdown(reason)}' + (f'\nDurée : {minutes} min' if minutes else ''), allowed_mentions=discord.AllowedMentions.none())
                    except discord.HTTPException:
                        logging.exception('Impossible d’envoyer le journal de modération')
        except discord.NotFound:
            await interaction.followup.send('Membre introuvable ou utilisateur non banni.', ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send('Discord a refusé cette action. Vérifie les permissions et la hiérarchie des rôles.', ephemeral=True)
        except discord.HTTPException:
            logging.exception('Échec de la sanction')
            await interaction.followup.send('Une erreur Discord a empêché l’action. Réessaie plus tard.', ephemeral=True)


# Le rôle de punition ne doit accorder aucun droit, y compris via les salons.
def safe_punishment_role(guild, role, me, actor):
    if (role is None or (role.id == guild.id or role.id in ROLE_IDS) or role.managed
            or role.permissions.value != 0 or role >= me.top_role
            or (actor.id != guild.owner_id and role >= actor.top_role)):
        return False
    return not any(channel.overwrites_for(role).pair()[0].value for channel in guild.channels)


PERMISSIONS = {
    'kick': 'kick_members', 'ban': 'ban_members', 'unban': 'ban_members',
    'timeout': 'moderate_members', 'untimeout': 'moderate_members',
    'nickname': 'manage_nicknames', 'reset_nickname': 'manage_nicknames',
    'role_add': 'manage_roles', 'role_remove': 'manage_roles',
    'voice_mute': 'mute_members', 'voice_unmute': 'mute_members',
    'voice_deafen': 'deafen_members', 'voice_undeafen': 'deafen_members',
    'voice_disconnect': 'move_members',
    'reminder_start': None, 'reminder_stop': None,
}

CATEGORIES = {
    'moderation': ('⚖️ Sanctions classiques', [
        ('Exclure un membre', 'kick', '👢'), ('Bannir un membre', 'ban', '🔨'),
        ('Mettre en sourdine temporaire', 'timeout', '⏳'), ('Retirer la sourdine', 'untimeout', '🔊'),
        ('Débannir un utilisateur', 'unban', '🔓'),
    ]),
    'fun': ('🎭 Punitions créatives', [
        ('Changer le surnom', 'nickname', '🤡'), ('Réinitialiser le surnom', 'reset_nickname', '✨'),
        ('Attribuer le rôle de punition', 'role_add', '🎭'), ('Retirer le rôle de punition', 'role_remove', '🕊️'),
        ('Activer les rappels en MP', 'reminder_start', '📨'), ('Arrêter les rappels en MP', 'reminder_stop', '📭'),
    ]),
    'voice': ('🎙️ Sanctions vocales', [
        ('Couper le micro', 'voice_mute', '🔇'), ('Rétablir le micro', 'voice_unmute', '🎤'),
        ('Assourdir en vocal', 'voice_deafen', '🙉'), ('Rétablir le son', 'voice_undeafen', '🎧'),
        ('Déconnecter du vocal', 'voice_disconnect', '🚪'),
    ]),
}


class ActionSelect(discord.ui.Select):
    def __init__(self, category):
        super().__init__(placeholder='Choisir une action…', options=[
            discord.SelectOption(label=title, value=kind, emoji=emoji)
            for title, kind, emoji in CATEGORIES[category][1]
        ])
        self.actions = {kind: {'title': title, 'kind': kind} for title, kind, _ in CATEGORIES[category][1]}

    async def callback(self, interaction):
        await interaction.response.send_modal(SanctionModal(self.actions[self.values[0]]))


class SanctionsView(GuardedView):
    def __init__(self, category):
        super().__init__()
        self.add_item(ActionSelect(category))


class MainView(GuardedView):
    async def open_category(self, interaction, category):
        title = CATEGORIES[category][0]
        embed = discord.Embed(title=title, description='Choisis une action. Le formulaire demande l’ID de la cible et un motif. **L’envoi du formulaire exécute immédiatement l’action.**', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, view=SanctionsView(category), ephemeral=True)

    @discord.ui.button(label='Sanctions classiques', emoji='⚖️', style=discord.ButtonStyle.danger)
    async def sanctions(self, interaction, button):
        await self.open_category(interaction, 'moderation')

    @discord.ui.button(label='Punitions créatives', emoji='🎭', style=discord.ButtonStyle.primary)
    async def fun(self, interaction, button):
        await self.open_category(interaction, 'fun')

    @discord.ui.button(label='Sanctions vocales', emoji='🎙️', style=discord.ButtonStyle.secondary)
    async def voice(self, interaction, button):
        await self.open_category(interaction, 'voice')


@bot.tree.command(name='panelemma', description='Ouvrir le panel privé d’Emma dictatrice', guild=discord.Object(id=GUILD_ID))
@app_commands.guild_only()
async def panelemma(interaction: discord.Interaction):
    if not allowed(interaction):
        return await deny(interaction)
    embed = discord.Embed(title='👑 Emma dictatrice', description='Bienvenue dans ton espace de modération privé. Choisis un panel ci-dessous.', color=discord.Color.dark_purple())
    embed.set_footer(text='Réservé au rôle autorisé • Les menus expirent après 15 minutes')
    await interaction.response.send_message(embed=embed, view=MainView(), ephemeral=True)


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    # Le destinataire peut toujours mettre fin lui-même aux rappels en MP.
    if message.guild is None:
        if message.content.strip().casefold() == 'stop' and REMINDERS.pop(message.author.id, None) is not None:
            try:
                await message.channel.send('Les rappels d’Emma sont arrêtés.')
            except discord.HTTPException:
                logging.warning('Impossible de confirmer l’arrêt des rappels en MP')
        return
    if message.guild.id != GUILD_ID:
        return
    reminder = REMINDERS.get(message.author.id)
    if reminder is None:
        return
    now = time.monotonic()
    if reminder['expires'] <= now or message.author.id == message.guild.owner_id or any(role.id in ROLE_IDS for role in message.author.roles):
        REMINDERS.pop(message.author.id, None)
        return
    # La limite reste appliquée même si un modérateur relance la punition.
    for user_id, sent_at in list(LAST_REMINDER_SENT.items()):
        if sent_at + REMINDER_INTERVAL <= now:
            LAST_REMINDER_SENT.pop(user_id, None)
    if now < reminder['next_send'] or message.author.id in LAST_REMINDER_SENT:
        return
    # Réserver le créneau avant le premier await pour éviter les envois concurrents.
    reminder['next_send'] = now + REMINDER_INTERVAL
    LAST_REMINDER_SENT[message.author.id] = now
    try:
        await message.author.send('👑 Emma te rappelle que tu es en punition ! Réponds STOP à ce message pour arrêter les rappels.', allowed_mentions=discord.AllowedMentions.none())
    except discord.Forbidden:
        # Les MP sont fermés : ne pas réessayer indéfiniment.
        if REMINDERS.get(message.author.id) is reminder:
            REMINDERS.pop(message.author.id, None)
    except discord.HTTPException:
        logging.exception('Impossible d’envoyer un rappel en MP')


@bot.event
async def on_ready():
    if not getattr(bot, '_synced', False):
        await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        bot._synced = True
    logging.info('Connecté en tant que %s', bot.user)


if __name__ == '__main__':
    bot.run(TOKEN)
