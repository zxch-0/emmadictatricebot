# 👑 Emma dictatrice

Bot Discord de modération pour **un seul serveur**, déployable avec un Blueprint Render. La seule commande est `/panelemma` : elle ouvre un menu privé, puis trois panels de sanctions classiques, créatives et vocales. Chaque action demande un ID et un motif ; envoyer le formulaire exécute immédiatement l’action. Les menus expirent au bout de 15 minutes : relancer la commande pour les rouvrir.

## Configuration Discord

1. Dans le [Discord Developer Portal](https://discord.com/developers/applications), crée une application et son bot, puis récupère son token. Ne le partage jamais et ne le mets pas dans le dépôt.
2. Invite le bot sur ton serveur via OAuth2 avec les scopes `bot` et `applications.commands`. Accorde-lui les permissions **Exclure des membres**, **Bannir des membres**, **Modérer les membres**, **Gérer les surnoms**, **Gérer les rôles**, **Couper le micro des membres**, **Mettre les membres en sourdine** (assourdir) et **Déplacer des membres**, selon les actions souhaitées, ainsi que **Voir les salons** et **Envoyer des messages** si tu utilises le journal.
3. Place le rôle du bot au-dessus des rôles des personnes qu’il doit modérer. Active le mode développeur dans Discord pour copier les IDs du serveur, du rôle autorisé et éventuellement du salon de journal.
4. Seuls les membres possédant au moins un des rôles configurés peuvent utiliser le panel, même s’ils sont administrateurs. Le rôle `@everyone` est interdit. Les membres portant un rôle autorisé, le propriétaire, l’auteur et le bot sont protégés ; la hiérarchie de l’auteur et du bot est également vérifiée. Discord applique aussi ses propres restrictions.

## Les panels

- **Classiques** : exclure, bannir, débannir, mettre en sourdine temporaire (1 à 40 320 minutes) et lever la sourdine.
- **Créatives** : changer le surnom (par exemple « Patate royale »), le réinitialiser, attribuer ou retirer un rôle de punition personnalisé, activer ou arrêter les rappels en MP. Utilise ces options avec humour et l’accord des membres concernés.
- **Vocales** : couper ou rétablir le micro, assourdir ou rétablir le son, déconnecter du vocal. La cible doit être connectée à un salon vocal. Ces changements de modération ne sont pas temporisés automatiquement.

**Rappels en MP** : configure une durée de 1 à 1 440 minutes. Quand la personne envoie un message sur le serveur, Emma peut lui envoyer un rappel privé, au maximum une fois par minute. Le destinataire peut répondre `STOP` en MP pour arrêter les rappels ; un modérateur peut aussi les arrêter depuis le panel. Si les MP sont fermés, les rappels s’arrêtent. Les rappels sont conservés uniquement en mémoire et disparaissent au redémarrage ou redéploiement du worker. Le bot doit voir les salons concernés ; il ne lit pas le contenu des messages du serveur. Utilise cette fonction avec l’accord de la personne.

Crée toi-même le rôle de punition et renseigne `PUNISHMENT_ROLE_ID` pour activer ces deux actions. Il doit être sous les rôles du bot et du modérateur, ne pas être géré par une intégration, ne pas être un des rôles autorisés ou `@everyone`, et ne posséder **aucune permission accordée**, ni au niveau du serveur ni dans les dérogations des salons. Tu peux configurer des dérogations qui refusent des permissions pour restreindre l’accès à certains salons. Vérifie leur effet en tenant compte des autres rôles du membre. Le rôle n’est pas retiré automatiquement. La réinitialisation du surnom supprime le surnom de serveur : l’ancien surnom n’est pas conservé.

## Déploiement sur Render

1. Publie ce dépôt sur GitHub puis, dans Render, crée un **Blueprint** en sélectionnant le dépôt et sa branche. Render utilise `render.yaml` pour créer un **Background Worker** (offre `starter`, payante selon les tarifs Render en vigueur).
2. Renseigne les variables demandées :
   - `DISCORD_TOKEN` : token secret du bot.
   - `GUILD_ID` : ID numérique du serveur unique.
   - `PANEL_ROLE_ID` : un ou deux IDs numériques de rôles autorisés, séparés par une virgule (exemple : `123456789,987654321`). Un seul ID reste accepté.
   - `LOG_CHANNEL_ID` : ID numérique du salon de journal ; laisse vide pour désactiver le journal.
   - `PUNISHMENT_ROLE_ID` : ID numérique du rôle de punition ; laisse vide pour désactiver les actions de rôle.
3. Lance le déploiement. Le bot synchronise la commande sur le serveur configuré au démarrage. Utilise ensuite `/panelemma` sur ce serveur.

Les sanctions sont enregistrées dans le journal d’audit Discord avec leur motif. Le journal optionnel est envoyé après l’action et son échec ne l’annule pas. Aucun stockage persistant ni intent privilégié n’est nécessaire. Le débannissement utilise l’ID d’un utilisateur banni ; les autres actions ciblent des membres actuellement présents.

## Lancement local

Python 3.12 recommandé :

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export DISCORD_TOKEN='ton-token' GUILD_ID='id-serveur' PANEL_ROLE_ID='123456789,987654321'
# Facultatif : export LOG_CHANNEL_ID='id-salon' PUNISHMENT_ROLE_ID='id-role-punition'
python bot.py
```

Ne versionne pas ton token. Les variables sont lues au démarrage : redémarre le worker après modification.

## Tests

Après installation des dépendances : `python -m unittest discover -s tests -v`. Ces tests simulent Discord ; vérifie également les permissions et les actions sur un serveur de test avant utilisation.
