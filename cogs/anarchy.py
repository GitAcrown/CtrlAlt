import asyncio
import json
import logging
import os
import random
import textwrap
import time
from datetime import datetime
from io import BytesIO
from typing import Dict, List, Optional, Tuple, Union

import discord
import yaml
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
from tabulate import tabulate

from common.dataio import get_package_path, get_sqlite_database
from common.utils import pretty

logger = logging.getLogger(f'ctrlalt.{__name__}')

CPU_NAMES = [
    "Sy Philis",
    "Doug Dick",
    "Mary Juana",
    "Rose Bud",
    "Sara Casm",
    "Sue Me",
    "Dill Doe",
    "Lou Sirr"
]

END_CARD_TEXT = [
    "{} qui a gagné la partie grâce au pouvoir de la discrimination positive.",
    "Réussir sa vie ? Non, mais {} a gagné une partie d'Anarchy.",
    "Que les loosers s'inclinent, {} a gagné.",
    "Qu'on mette une croix dans le calendrier, parce {} a enfin gagné.",
    "[Insertion d'un son de trompette] {} a gagné.",
    "[Insérer une blague drôle], {} !!!",
    "{} qui gagne, c'est comme un chien qui fait pipi sur un arbre, ça arrive.",
    "{} qui remporte la partie parce qu'il est le plus raciste de tous.",
    "{} qui a gagné la partie parce qu'il est le plus homophobe de tous.",
    "{} qui gagne la partie car c'est le plus xénophobe de tous.",
    "{} qui a gagné la partie parce qu'il a le plus gros pénis.",
    "{} qui a gagné la partie parce qu'il a le plus gros cul.",
    "A ce jeu, c'est le plus con qui l'emporte, et {} a gagné.",
    "L'humour noir est un art, et {} l'a parfaitement maîtrisé.",
    "La victoire est à {} ! Je suis fier de lui.",
    "Même si c'est pas mérité, {} a gagné.",
    "La partie était nulle, mais {} a gagné et il faut respecter ça.",
    "Désolé les gars, mais c'est {} qui l'emporte.",
    "C'est {} qui a gagné la partie, mais on s'en fout.",
    "Quand on est con, on est con, et {} est le/la plus con(ne).",
    "Xenophobe, homophobe et raciste, je vous présente {}.",
    "{} qui gagne c'est du jamais vu, mais c'est arrivé."
]

MAX_PLAYERS = 8
MINIMAL_HUMAN_PLAYERS = 2
FILL_PLAYERS_UNTIL = 4
HAND_SIZE = 6
WINNER_POINTS = 3
VOTED_POINTS = 1
TIMEOUTS = {
    'register': 60,
    'choose_cards': 60,
    'select_cardpacks': 30,
    'play_round': 90,
    'vote_round': 60,
    'export_black_cards': 30
}

# Vues Discord ----------------------------------------------------------------

# Choix des extensions de cartes
class ChoosePacksSelect(discord.ui.Select):
    def __init__(self, game: 'ClassicGame', packs: List['CardsPack']) -> None:
        super().__init__(
            placeholder="Choisissez les extensions de cartes à utiliser",
            min_values=1,
            max_values=len(packs),
            row=0
        )
        self.game = game
        self.packs = packs
        self.__fill_options()
        
    def __fill_options(self) -> None:
        for pack in self.packs:
            self.add_option(label=pack.name, value=pack.id, description=pack.description, emoji=pack.emoji)

    async def callback(self, interaction: discord.Interaction) -> None:
        packs = [pack for pack in self.packs if pack.id in self.values]
        self.game._load_cards(packs)
        pack_txt = '\n'.join([f'• **{pack.name}** `[{len(pack.black_cards)}B| {len(pack.white_cards)}W]`' for pack in packs])
        await interaction.response.send_message(f"**Extensions ajoutées à la partie ·** Packs de cartes chargés :\n{pack_txt}", ephemeral=True, delete_after=10)
    
# Enregistrement des joueurs
class RegisterPlayersView(discord.ui.View):
    def __init__(self, game: 'ClassicGame') -> None:
        super().__init__(timeout=TIMEOUTS['register'])
        self.game = game
        self.message : discord.Message = None #type: ignore
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        selfplayer = HumanPlayer(interaction.user)
        if selfplayer not in self.game.players:
            return True
        await interaction.response.send_message("**Erreur ·** Vous êtes déjà inscrit à la partie", ephemeral=True, delete_after=10)
        return False
    
    def get_embed(self, starting: bool = False) -> discord.Embed:
        desc = f"**{self.game.author.name}** vous invite à jouer à Anarchy !\nRejoignez la partie en cliquant sur le bouton ci-dessous ({TIMEOUTS['register']}s)"
        if starting:
            desc = f"**Inscriptions terminées**\nLa partie va bientôt commencer !"
        embed = discord.Embed(
            title="**Anarchy ·** Inscription à la partie",
            description=desc,
            color=discord.Color.blurple()
        )
        embed.add_field(name="Extensions utilisées", value='\n'.join([f'• **{pack.name}** `[{len(pack.black_cards)}B|{len(pack.white_cards)}W]`' for pack in self.game.packs]))
        embed.add_field(name="Nombre de rounds", value=f"**{self.game.rounds}** (Env. {int(self.game.rounds * 2)} min.)")
        embed.add_field(name=f"Joueurs inscrits ({len(self.game.players)}/{MAX_PLAYERS})", value='\n'.join([f'• **{player}**' for player in self.game.players]), inline=False)
        return embed
    
    async def start(self) -> None:
        embed = self.get_embed()
        self.message = await self.game.channel.send(embed=embed, view=self)
        
    async def on_timeout(self) -> None:
        if len(self.game.players) < MINIMAL_HUMAN_PLAYERS:
            await self.message.edit(view=None)
        elif len(self.game.players) < FILL_PLAYERS_UNTIL:
            self.game.fill_players()
            embed = self.get_embed(starting=True)
            embed.set_footer(text=f"🤖 Des IA ont été ajoutées à la partie pour atteindre {FILL_PLAYERS_UNTIL} joueurs\nLa partie sera enregistrée dans le but d'améliorer les CPU")
            await self.message.edit(embed=embed, view=None)
        self.stop()
        
    @discord.ui.button(label="Rejoindre la partie", style=discord.ButtonStyle.blurple)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Rejoindre la partie"""
        if len(self.game.players) >= MAX_PLAYERS:
            await interaction.response.send_message("**Erreur ·** La partie est déjà pleine", ephemeral=True)
            return
        player = HumanPlayer(interaction.user)
        self.game.add_player(player)
        await self.message.edit(embed=self.get_embed())
        await interaction.response.send_message(f"**Anarchy ·** Vous avez rejoint la partie", ephemeral=True, delete_after=20)
        
# Choix des cartes à jouer
class ChooseCardsView(discord.ui.View):
    def __init__(self, game: 'ClassicGame') -> None:
        super().__init__(timeout=None)
        self.game = game
        self.message : discord.Message = None #type: ignore
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        player = self.game.get_player_by_id(interaction.user.id)
        if player is None:
            await interaction.response.send_message("**Erreur ·** Vous ne jouez pas à la partie en cours", ephemeral=True, delete_after=10)
            return False
        if player.played_cards:
            await interaction.response.send_message("**Erreur ·** Vous avez déjà joué", ephemeral=True, delete_after=10)
            return False
        if player.status == 'choosing':
            await interaction.response.send_message("**Erreur ·** Vous êtes déjà en train de sélectionner vos cartes !", ephemeral=True, delete_after=10)
            return False
        return True

    async def start(self) -> None:
        image = self.game.round_black_card.image
        self.message = await self.game.channel.send(content="**Voici la carte noire de ce round ·** Cliquez sur le bouton ci-dessous pour proposer vos cartes.\n_ _", file=image, view=self)
        
    @discord.ui.button(label='Proposer ses cartes', emoji='<:iconCards:1078392002086969344>', style=discord.ButtonStyle.green)
    async def play_round(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Jouer le round"""
        player = self.game.get_player_by_id(interaction.user.id)
        if not player:
            return
        view = discord.ui.View(timeout=TIMEOUTS['choose_cards'])
        select = ChooseWhiteCardsSelect(self.game, player, self.game.round_black_card.blanks)
        view.add_item(select)
        player.status = 'choosing'
        await interaction.response.send_message(f"**Anarchy ·** Choisissez vos cartes à jouer pour compléter les trous de la carte noire.", ephemeral=True, view=view)
        await view.wait()
        await interaction.edit_original_response(view=None)
        
# Element de sélection des cartes à jouer
class ChooseWhiteCardsSelect(discord.ui.Select):
    def __init__(self, game: 'ClassicGame', player: 'Player', picks: int) -> None:
        super().__init__(
            placeholder=f"Choississez une carte" if picks == 1 else f"Choississez {picks} cartes (dans l'ordre)",
            min_values=picks,
            max_values=picks,
            row=0
        )
        self.game = game
        self.player = player
        self.picks = picks
        self.__fill_options()
        
    def __fill_options(self) -> None:
        for card in self.player.hand:
            self.add_option(label=f"{card}", value=card)
    
    async def callback(self, interaction: discord.Interaction) -> None:
        if self.game.status != 'choose_cards':
            return await interaction.response.send_message("**Erreur ·** Vous ne pouvez plus jouer de cartes pour le moment", ephemeral=True, delete_after=10)
        edited = False
        if self.player.played_cards:
            self.player.cancel_play()
            edited = True
        self.player.play(self.values)
        bc_demo = self.game.round_black_card.fill(self.values, with_codeblock=True)
        if edited:
            await interaction.response.send_message(f"**Carte(s) modifiée(s) ·** Vous avez joué {' '.join((f'`{value}`' for value in self.values))}.\n\n✱ **{bc_demo}**", ephemeral=True, delete_after=20)
        else:
            await interaction.response.send_message(f"**Carte(s) jouée(s) ·** Vous avez joué {' '.join((f'`{value}`' for value in self.values))}.\n\n✱ **{bc_demo}**", ephemeral=True, delete_after=20)
        
# Vote pour la meilleure carte
class VoteBestCardsSelect(discord.ui.Select):
    def __init__(self, game: 'ClassicGame') -> None:
        super().__init__(
            placeholder=f"Sélectionnez votre proposition favorite",
            min_values=1,
            max_values=1,
            row=0
        )
        self.game = game
        self.__fill_options()
        
    def __fill_options(self) -> None:
        black_card = self.game.round_black_card
        for player_id, cards in self.game.round_white_cards.items():
            self.add_option(label=" | ".join(cards), value=player_id, description=pretty.troncate_text(black_card.fill(cards), 100))
    
    async def callback(self, interaction: discord.Interaction) -> None:
        selfplayer = self.game.get_player_by_id(interaction.user.id)
        if not selfplayer:
            return await interaction.response.send_message("**Erreur ·** Vous ne jouez pas à la partie en cours", ephemeral=True, delete_after=10)
        if self.game.status != 'vote_round':
            return await interaction.response.send_message("**Erreur ·** Vous ne pouvez plus voter pour le moment", ephemeral=True, delete_after=10)
        edited = False
        if selfplayer in [player for pid in self.game.votes for player in self.game.votes[pid]]:
            edited = True
            self.game.clear_player_vote(selfplayer)
        if not self.game.add_vote(selfplayer, self.values[0]):
            return await interaction.response.send_message(f"**Erreur ·** Vous ne pouvez pas voter pour votre propre proposition.", ephemeral=True, delete_after=10)
        
        cards = self.game.round_white_cards[self.values[0]]
        for c in cards:
            self.game.white_cards_human[c] = self.game.white_cards_human.get(c, 0) + 1
            
        if edited:
            await interaction.response.send_message(f"**Vote modifié ·** Vous avez voté pour {' | '.join(f'`{c}`' for c in cards)}.", ephemeral=True, delete_after=20)
        else:
            await interaction.response.send_message(f"**Vote enregistré ·** Vous avez voté pour {' | '.join(f'`{c}`' for c in cards)}.", ephemeral=True, delete_after=20)

# Boutons d'export des cartes noires complétées
class ExportBlackCardsView(discord.ui.View):
    def __init__(self, game: 'ClassicGame') -> None:
        super().__init__(timeout=TIMEOUTS['export_black_cards'])
        self.game = game
        self.files = self.__get_files()
        self.receivers = []
        
    def __get_files(self) -> List[discord.File]:
        black_card = self.game.round_black_card
        winners = [(self.game.round_white_cards[str(player.id)], player) for player in self.game.get_winners()]
        files = []
        for winner_text, player in winners:
            file = black_card.fill_image(winner_text, footer=f"@{str(player)}")
            files.append(file)
        return files
        
    @discord.ui.button(label='Exporter les cartes noires', style=discord.ButtonStyle.gray)
    async def export_black_cards(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Obtenir les cartes noires complétées"""
        await interaction.response.send_message(f"**Exportation des cartes noires (Round {self.game.round}) ·** Voici les cartes noires complétées avec les propositions des gagnants.", 
                                                files=self.files, 
                                                ephemeral=True)
        self.receivers.append(interaction.user.id)
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self.receivers:
            await interaction.response.send_message(f"**Erreur ·** Vous avez déjà reçu les cartes noires demandées.", ephemeral=True, delete_after=15)
            return False
        return True
        
    async def on_timeout(self) -> None:
        self.export_black_cards.disabled = True
        self.stop()
    
            
# Classes de jeu ---------------------------------------------------------------

class CPUTraining:
    def __init__(self, cog: 'Anarchy') -> None:
        self._cog = cog
        self.data = {}
        
    def register_round(self, black_card: 'BlackCard', white_cards: Dict[str, int]) -> None:
        self.data[black_card.text] = white_cards
    
    def save(self) -> None:
        self._cog.update_training_data(self.data)


class Player:
    def __init__(self) -> None:
        self.id = int(time.time())
        self.score = 0
        self.hand = []
        self.played_cards = []
        self.status = 'idle'
        
    def __eq__(self, __o: object) -> bool:
        if isinstance(__o, Player):
            return self.id == __o.id
        return False
        
    def __len__(self) -> int:
        return len(self.hand)
    
    def __hash__(self) -> int:
        return hash(self.id)
    
    def draw(self, cards: List[str]) -> None:
        """Piocher des cartes"""
        self.hand.extend(cards)
        
    def play(self, cards: List[str]) -> None:
        """Jouer des cartes"""
        for card in cards:
            self.hand.remove(card)
        self.played_cards.extend(cards)
    
    def cancel_play(self) -> None:
        """Annuler le dernier tour"""
        self.hand.extend(self.played_cards)
        self.played_cards.clear()
    
class HumanPlayer(Player):
    def __init__(self, user: Union[discord.User, discord.Member]) -> None:
        super().__init__()
        self.id = user.id
        self.user = user
    
    def __str__(self) -> str:
        return self.user.name
    
class BotPlayer(Player):
    def __init__(self, cog: 'Anarchy', name: str) -> None:
        super().__init__()
        self._cog = cog
        self.id = name.lower()
        self.name = name
        
        self.brain = self.__training_data()
        
    def __str__(self) -> str:
        return self.name + ' <CPU>'
    
    def __training_data(self) -> Dict[str, Dict[str, int]]:
        return self._cog.get_training_data()
    
    def _get_best_cards(self, black_card: 'BlackCard') -> List[str]:
        """Retourne les cartes les plus adaptées à la carte noire"""
        if black_card.text not in self.brain:
            return []
        data_cards = {card: score for card, score in self.brain[black_card.text].items() if card in self.hand}
        if not data_cards:
            return []
        return sorted(data_cards, key=data_cards.get, reverse=True) #type: ignore
    
    def submit_cards(self, black_card: 'BlackCard') -> None:
        """Soumettre des cartes pour le round"""
        best_cards = self._get_best_cards(black_card)
        if len(best_cards) >= black_card.blanks:
            cards = best_cards[:black_card.blanks]
        else:
            cards = random.sample(self.hand, black_card.blanks)
        self.play(cards)
        
    def vote(self, white_cards: Dict[str, List[Player]]) -> str:
        """Voter pour une proposition au hasard"""
        return random.choice(list(white_cards.keys()))

    
class CardsPack:
    def __init__(self, pack_data: dict) -> None:
        self._data = pack_data
        self.id : str = pack_data['id']
        self.name : str = pack_data['name']
        self.description : str = pack_data['short']
        self.emoji : str = pack_data['emoji']
        self.author : str = pack_data['author']
        self.last_update = datetime.now().strptime(pack_data['last_update'], '%d-%m-%Y')
        self.guilds : List[int] = pack_data.get('guilds', [])
        
        self.black_cards = [BlackCard(card) for card in pack_data['black_cards']]
        self.white_cards = pack_data['white_cards']
        
    def __str__(self) -> str:
        return f"{self.name} `[{len(self.black_cards)}B|{len(self.white_cards)}W]`"
    
    def __eq__(self, __o: object) -> bool:
        if isinstance(__o, CardsPack):
            return self.id == __o.id
        return False
     
    def __hash__(self) -> int:
        return hash(self.id)
    
    def is_available(self, guild: discord.Guild) -> bool:
        if self.guilds:
            return guild.id in self.guilds
        return True
    
class BlackCard:
    def __init__(self, text: str) -> None:
        self.text = text
        self.blanks = text.count('_')
        
    def __str__(self) -> str:
        return self.text.replace('_', '________')
    
    def __eq__(self, __o: object) -> bool:
        if isinstance(__o, BlackCard):
            return self.text == __o.text
        return False
    
    def __hash__(self) -> int:
        return hash(self.text)
    
    def fill(self, cards: List[str], with_codeblock: bool = False) -> str:
        if len(cards) != self.blanks:
            raise ValueError(f'Expected {self.blanks} cards, got {len(cards)}')
        if not with_codeblock:
            return self.text.replace('_', '{}').format(*cards)
        return self.text.replace('_', '`{}`').format(*cards)
    
    def wrap_blanks(self) -> str:
        return self.text.replace('_', "`________`")
    
    def __add_corners(self, im, rad):
        circle = Image.new('L', (rad * 2, rad * 2), 0)
        draw = ImageDraw.Draw(circle)
        draw.ellipse((0, 0, rad * 2, rad * 2), fill=255)
        alpha = Image.new('L', im.size, "white")
        w, h = im.size
        alpha.paste(circle.crop((0, 0, rad, rad)), (0, 0))
        alpha.paste(circle.crop((0, rad, rad, rad * 2)), (0, h - rad))
        alpha.paste(circle.crop((rad, 0, rad * 2, rad)), (w - rad, 0))
        alpha.paste(circle.crop((rad, rad, rad * 2, rad * 2)), (w - rad, h - rad))
        im.putalpha(alpha)
        return im

    def _generate_image(self, text: str, horizontal: bool = True, footer: str = ''):
        path = get_package_path('anarchy')
        imgdim = (750, 500) if horizontal else (500, 750)
        img = Image.new('RGB', imgdim, 'black')
        d = ImageDraw.Draw(img)
        font = ImageFont.truetype(f'{path}/assets/Coolvetica.otf', 40, encoding='unic')
        wrapped = textwrap.wrap(text, width=39 if horizontal else 24)
        
        d.text((34, 30), '\n'.join(wrapped), font=font, fill='white')
        
        logo_font = ImageFont.truetype(f'{path}/assets/Coolvetica.otf', 30, encoding='unic')
        d.text((imgdim[0] - 60, imgdim[1] - 70), '*', font=font, fill='white')
        d.text((imgdim[0] - 165, imgdim[1] - 70), 'Anarchy', font=logo_font, fill='white')
        
        if footer:
            d.text((36, imgdim[1] - 70), f'{footer}', font=logo_font, fill='white')
        
        img = self.__add_corners(img, 30)
        return img
    
    @property
    def image(self) -> discord.File:
        with BytesIO() as image_binary:
            self._generate_image(self.__str__()).save(image_binary, 'PNG')
            image_binary.seek(0)
            return discord.File(fp=image_binary, filename='black_card.png', description=self.__str__())
    
    def fill_image(self, cards: List[str], footer: str = '') -> discord.File:
        """Créer une image de la carte noire remplie avec les cartes blanches voulues"""
        with BytesIO() as image_binary:
            self._generate_image(self.fill(cards), footer=footer).save(image_binary, 'PNG')
            image_binary.seek(0)
            return discord.File(fp=image_binary, filename='black_card.png', description=self.__str__())

class ClassicGame:
    """Logique de jeu pour une partie de Anarchy classique"""
    
    def __init__(self, cog: 'Anarchy', channel: Union[discord.TextChannel, discord.Thread], rounds: int, author: Union[discord.User, discord.Member]) -> None:
        self._cog = cog
        self.channel = channel
        self.rounds = rounds
        self.author = author
        
        self.training = CPUTraining(self._cog)
        
        self.packs : List[CardsPack] = []
        self.players : List[Player] = []
        self.round = 0
        
        self.round_black_card : BlackCard = None #type: ignore
        self.black_cards : List[BlackCard] = []
        
        self.round_white_cards : Dict[str, list] = {}
        self.white_cards : List[str] = []
        
        self.used_black_cards : List[BlackCard] = []
        self.used_white_cards : List[str] = []
        
        self.votes : Dict[str, List[Player]] = {}
        self.voters : List[Player] = []
        self.white_cards_human : Dict[str, int] = {}
        
        self.status = 'register'
    
    def _load_cards(self, packs: List[CardsPack]) -> None:
        self.packs = packs
        self.black_cards = list(set([card for pack in packs for card in pack.black_cards]))
        random.shuffle(self.black_cards)
        self.white_cards = list(set([card for pack in packs for card in pack.white_cards]))
        random.shuffle(self.white_cards)
        
    # Players =================
        
    def add_player(self, player: Player) -> None:
        self.players.append(player)
    
    def remove_player(self, player: Player) -> None:
        self.players.remove(player)
        
    def get_player_by_id(self, player_id: Union[int, str]) -> Optional[Player]:
        player_id = str(player_id)
        for player in self.players:
            if str(player.id) == player_id:
                return player
        return None
    
    def fill_players(self) -> None:
        names = CPU_NAMES.copy()
        while len(self.players) < FILL_PLAYERS_UNTIL:
            name = names.pop(random.randint(0, len(names) - 1))
            self.add_player(BotPlayer(self._cog, name))
            
    # Cartes ==================
    
    def draw_black_card(self) -> BlackCard:
        if not self.black_cards:
            self.black_cards = self.used_black_cards
            self.used_black_cards = []
            random.shuffle(self.black_cards)
        card = self.black_cards.pop()
        self.used_black_cards.append(card)
        return card
    
    def draw_white_card(self) -> str:
        if not self.white_cards:
            self.white_cards = self.used_white_cards
            self.used_white_cards = []
            random.shuffle(self.white_cards)
        card = self.white_cards.pop()
        self.used_white_cards.append(card)
        return card
    
    def fill_players_hands(self) -> None:
        for player in self.players:
            pcards = [self.draw_white_card() for _ in range(HAND_SIZE - len(player.hand))]
            player.draw(pcards)
                
    def cpu_submit_cards(self) -> None:
        for player in self.players:
            if isinstance(player, BotPlayer):
                player.submit_cards(self.round_black_card)
                
    def fetch_round_cards(self) -> None:
        self.round_white_cards = {}
        for player in self.players:
            if not isinstance(player, Player):
                continue
            self.round_white_cards[str(player.id)] = player.played_cards
            player.played_cards = []
        
    # Votes ===================
    
    def add_vote(self, player: Player, voted_player_id: str) -> bool:
        voted = self.get_player_by_id(voted_player_id)
        if not voted:
            return False
        if voted.id == player.id:
            return False # On ne peut pas voter pour soi-même
        if not voted_player_id in self.votes:
            self.votes[voted_player_id] = []
        self.votes[voted_player_id].append(player)
        self.voters.append(player)
        return True
    
    def clear_player_vote(self, player: Player) -> None:
        for player_id, voters in self.votes.items():
            if player in voters:
                self.votes[player_id].remove(player)
                self.voters.remove(player)
                
    def cpu_votes(self) -> None:
        for player in self.players:
            if isinstance(player, BotPlayer):
                while self.add_vote(player, player.vote(self.round_white_cards)) == False:
                    pass
                
    def fetch_votes(self) -> Dict[Player, int]:
        votes = {}
        for player_id, voters in self.votes.items():
            player = self.get_player_by_id(player_id)
            votes[player] = len(voters)
        return votes
    
    def get_winners(self) -> List[Player]:
        votes = self.fetch_votes()
        winners = [k for k, v in votes.items() if v == max(votes.values())]
        return winners
                
    # Vues ===================
    
    async def select_cardpacks(self, original_interaction: discord.Interaction) -> bool:
        guild = original_interaction.guild
        if not guild:
            return False
        packs = [pack for pack in self._cog.Packs if pack.is_available(guild)]
        
        view = discord.ui.View(timeout=TIMEOUTS['select_cardpacks'])
        view.add_item(ChoosePacksSelect(self, packs))
        await original_interaction.response.send_message('Choisissez les packs de cartes à utiliser pour cette partie', view=view, ephemeral=True)
        while not self.black_cards and not self.white_cards and not view.is_finished():
            await asyncio.sleep(0.5)
        if not self.black_cards and not self.white_cards:
            return False
        await original_interaction.edit_original_response(view=None)
        view.stop()
        return True
    
    async def register_players(self) -> bool:
        view = RegisterPlayersView(self)
        await view.start()
        await view.wait()
        if len(self.players) < MINIMAL_HUMAN_PLAYERS:
            return False
        return True
    
    # Jeu ====================
    
    async def start_game(self) -> bool:
        await self.channel.send("**Anarchy ·** La partie va bientôt commencer !", delete_after=20)
        await asyncio.sleep(3)
        while self.round < self.rounds:
            self.round += 1
            await self.start_round()
        await self.end_game()
        return True
    
    async def start_round(self) -> None:
        # Initialisation du round
        self.fill_players_hands()
        await self.channel.send(f"**~~            ~~ Round {self.round} ~~            ~~**\nVos cartes blanches ont été distribuées.")
        
        # Choix de la carte noire
        self.round_black_card = self.draw_black_card()
        await asyncio.sleep(2.5)
        
        # Affichage de la carte noire et proposition des cartes blanches
        self.status = 'choose_cards'
        choosecardsview = ChooseCardsView(self)
        await choosecardsview.start()
        self.cpu_submit_cards() # On fait jouer les bots
        timeout = time.time() + TIMEOUTS['play_round']
        while len([p for p in self.players if p.played_cards]) < len(self.players) and time.time() < timeout:
            await asyncio.sleep(0.5)
        await asyncio.sleep(2)
        choosemsg = choosecardsview.message
        choosecardsview.stop()
        self.status = 'idle'
        await choosemsg.edit(view=None)
        
        if len([p for p in self.players if p.played_cards]) < len(self.players):
            await self.channel.send(f"**Round {self.round} ·** Temps écoulé ! Tous les joueurs qui n'ont pas joué ne pourront recevoir de points.")
        else:
            await self.channel.send(f"**Round {self.round} ·** Tous les joueurs ont joué ! Préparez-vous à voter...")
            
        for player in self.players:
            player.status = 'idle'
        await asyncio.sleep(4.5)
        
        # Vote de la meilleure carte blanche
        self.fetch_round_cards()
        await asyncio.sleep(1)
        self.votes = {}
        self.voters = []
        self.white_cards_human = {}
        self.status = 'vote_round'
        await self.channel.send(f"**~~          ~~ Ouverture des votes ~~          ~~**")
        embed = discord.Embed(description=f"***{self.round_black_card.wrap_blanks()}***", color=discord.Color.blurple())
        embed.set_image(url=choosemsg.attachments[0].url)
        embed.set_footer(text=f"Round {self.round} · Votez pour la carte blanche qui vous semble la plus drôle !")
        voteview = discord.ui.View(timeout=None)
        voteview.add_item(VoteBestCardsSelect(self))
        votemsg = await self.channel.send(embed=embed, view=voteview)
        self.cpu_votes() # On fait voter les bots
        timeout = time.time() + TIMEOUTS['vote_round']
        while len(self.voters) < len(self.players) and time.time() < timeout:
            await asyncio.sleep(0.5)
        await asyncio.sleep(4)
        self.status = 'idle'
        voteview.stop()
        await votemsg.edit(view=None)
        all_voters = self.voters.copy()
        if len(self.voters) < len(self.players):
            await self.channel.send(f"**Round {self.round} ·** Temps écoulé ! Les joueurs n'ayant pas voté perdent un point.")
            for player in self.players:
                if player not in all_voters:
                    player.score = max(0, player.score - 1)
        else:
            await self.channel.send(f"**Round {self.round} ·** Tous les joueurs ont voté !")
            
        self.training.register_round(self.round_black_card, self.white_cards_human)
            
        for player in self.players:
            player.status = 'idle'
        await asyncio.sleep(5)
        
        # Annonce du gagnant du round
        votes = self.fetch_votes()
        winners = self.get_winners()
        for player in winners:
            player.score += WINNER_POINTS
        for player in votes:
            if votes[player] > 0:
                player.score += VOTED_POINTS
        
        em = discord.Embed(title=f"**Round {self.round} ·** Résultats", color=discord.Color.blurple())
        winners_txt = "\n".join([f"**{player}** · {self.round_black_card.fill(self.round_white_cards[str(player.id)], with_codeblock=True)}" for player in winners])
        em.add_field(name=f"Gagnant(s) ({max(votes.values())} votes)", value=winners_txt)
        em.add_field(name="Scores", value="\n".join([f"• **{player}** · {player.score} points" for player in self.players]), inline=False)
        em.set_footer(text=f"Les gagnants ont reçu 3 points et ceux ayant eu au moins un vote ont reçu 1 point.")
        await self.channel.send(embed=em, view=ExportBlackCardsView(self))
        
        if self.round < self.rounds:
            await asyncio.sleep(14)
        else:
            await asyncio.sleep(8)
    
    async def end_game(self) -> None:
        await self.channel.send("**~~            ~~ Fin de la partie ~~            ~~**")
        await asyncio.sleep(1.5)
        
        winners = [player for player in self.players if player.score == max([p.score for p in self.players])]
        path = get_package_path('anarchy')
        
        if len(winners) == 1:
            textcard = random.choice(END_CARD_TEXT).format(winners[0])
            if isinstance(winners[0], HumanPlayer):
                userpfp = await winners[0].user.display_avatar.read()
                userpfp = Image.open(BytesIO(userpfp))
            else:
                userpfp = Image.open(f"{path}/assets/bot_image.png")
                
            with BytesIO() as image_binary:
                winner_img = await self._cog.generate_end_card_img(userpfp, textcard)
                winner_img.save(image_binary, 'PNG')
                image_binary.seek(0)
                await self.channel.send(f"**Anarchy ·** La partie est terminée !\nFélicitations à **{winners[0]}** pour sa victoire !", file=discord.File(fp=image_binary, filename='winner.png', description=textcard))
        else:
            await self.channel.send(f"**Anarchy ·** La partie est terminée !\nFélicitations à **{', '.join([str(w) for w in winners])}** pour leur victoire !")
        
        for winner in [w for w in winners if isinstance(w, HumanPlayer)]:
            self._cog.update_player_score(self.channel.guild, winner.user)
            
        self.training.save()
        
# COG -------------------------------------------------------------------------------------------------------------------------------
        
class Anarchy(commands.GroupCog, name="anarchy", description="Jeu inspiré de Cards Against Humanity"):
    """Jeu inspiré de Cards Against Humanity"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions = []
    
    @commands.Cog.listener()
    async def on_ready(self):
        self.Packs = self.__load_package_files()
        self.__initialize_database()
        
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self.__initialize_database(guild)
    
    def __load_package_files(self) -> List[CardsPack]:
        files = get_package_path('anarchy')
        packs = []
        for file in os.listdir(files):
            if file.endswith(".yaml"):
                with open(os.path.join(files, file), 'r', encoding='utf-8') as f:
                    pack = yaml.safe_load(f)
                    packs.append(CardsPack(pack))
        return packs
    
    def __initialize_database(self, guild: Optional[discord.Guild] = None):
        conn = get_sqlite_database('anarchy')
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS training (black_card TEXT PRIMARY KEY, white_cards LONGTEXT)")
        conn.commit()
        cursor.close()
        conn.close()
        
        guilds = [guild] if guild else self.bot.guilds
        for g in guilds:
            conn = get_sqlite_database('anarchy', f'g{g.id}')
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS players (user_id INTEGER PRIMARY KEY, score INTEGER DEFAULT 0)")
            conn.commit()
            cursor.close()
            conn.close()
            
    def update_player_score(self, guild: discord.Guild, user: Union[discord.User, discord.Member]):
        conn = get_sqlite_database('anarchy', f'g{guild.id}')
        cursor = conn.cursor()
        cursor.execute("SELECT score FROM players WHERE user_id = ?", (user.id,))
        current_score = cursor.fetchone()
        new_score = current_score[0] + 1 if current_score else 1
        
        cursor.execute("INSERT OR REPLACE INTO players (user_id, score) VALUES (?, ?)", (user.id, new_score))
        conn.commit()
        cursor.close()
        conn.close()
        
    def get_players_scores(self, guild: discord.Guild) -> List[Tuple[int, int]]:
        conn = get_sqlite_database('anarchy', f'g{guild.id}')
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, score FROM players ORDER BY score DESC")
        players = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return players
            
    def update_training_data(self, data: dict):
        current_data = self.get_training_data()
        conn = get_sqlite_database('anarchy')
        cursor = conn.cursor()
        for black_card, white_cards in data.items():
            if black_card not in current_data:
                current_data[black_card] = {}
            for white_card, count in white_cards.items():
                current_data[black_card][white_card] = current_data[black_card].get(white_card, 0) + count
            cursor.execute("INSERT OR REPLACE INTO training (black_card, white_cards) VALUES (?, ?)", (black_card, json.dumps(white_cards)))
        conn.commit()
        cursor.close()
        conn.close()
            
    def get_training_data(self) -> Dict[str, Dict[str, int]]:
        conn = get_sqlite_database('anarchy')
        cursor = conn.cursor()
        cursor.execute("SELECT black_card, white_cards FROM training")
        data = cursor.fetchall()
        cursor.close()
        conn.close()
        return {black_card: json.loads(white_cards) for black_card, white_cards in data}
    
    def __add_corners(self, im, rad):
        circle = Image.new('L', (rad * 2, rad * 2), 0)
        draw = ImageDraw.Draw(circle)
        draw.ellipse((0, 0, rad * 2, rad * 2), fill=255)
        alpha = Image.new('L', im.size, "white")
        w, h = im.size
        alpha.paste(circle.crop((0, 0, rad, rad)), (0, 0))
        alpha.paste(circle.crop((0, rad, rad, rad * 2)), (0, h - rad))
        alpha.paste(circle.crop((rad, 0, rad * 2, rad)), (w - rad, 0))
        alpha.paste(circle.crop((rad, rad, rad * 2, rad * 2)), (w - rad, h - rad))
        im.putalpha(alpha)
        return im
    
    async def generate_end_card_img(self, user_image, text: str):
        path = get_package_path('anarchy')
        userpfp = user_image.resize((440, 440)).convert('RGBA')
        userpfp = self.__add_corners(userpfp, 16)
        
        imgdim = (500, 750)
        img = Image.new('RGB', imgdim, 'white')
        d = ImageDraw.Draw(img)
        font = ImageFont.truetype(f'{path}/assets/Coolvetica.otf', 36, encoding='unic')
        wrapped = textwrap.wrap(text, width=28)
        
        d.text((34, 482), '\n'.join(wrapped), font=font, fill='black')
        
        logo_font = ImageFont.truetype(f'{path}/assets/Coolvetica.otf', 30, encoding='unic')
        d.text((imgdim[0] - 60, imgdim[1] - 70), '*', font=font, fill='black')
        d.text((imgdim[0] - 165, imgdim[1] - 70), 'Anarchy', font=logo_font, fill='black')
        
        img.paste(userpfp, (30, 32), userpfp)
        img = self.__add_corners(img, 30)
        return img
    
    def _generate_white_card(self, text: str, horizontal: bool = True):
        path = get_package_path('anarchy')
        imgdim = (750, 500) if horizontal else (500, 750)
        img = Image.new('RGB', imgdim, 'white')
        d = ImageDraw.Draw(img)
        font = ImageFont.truetype(f'{path}/assets/Coolvetica.otf', 40, encoding='unic')
        wrapped = textwrap.wrap(text, width=39 if horizontal else 24)
        
        d.text((34, 30), '\n'.join(wrapped), font=font, fill='black')
        
        logo_font = ImageFont.truetype(f'{path}/assets/Coolvetica.otf', 30, encoding='unic')
        d.text((imgdim[0] - 60, imgdim[1] - 70), '*', font=font, fill='black')
        d.text((imgdim[0] - 165, imgdim[1] - 70), 'Anarchy', font=logo_font, fill='black')
        
        img = self.__add_corners(img, 30)
        return img
    
    def _generate_gold_card(self, text: str, horizontal: bool = True):
        imgdim = (750, 500) if horizontal else (500, 750)
        img = Image.open('cogs/packages/anarchy/assets/gold_texture.jpg', 'r').convert('RGBA')
        img = img.resize((imgdim[0], imgdim[1]))
        d = ImageDraw.Draw(img)
        font = ImageFont.truetype(f'cogs/packages/anarchy/assets/Coolvetica.otf', 40, encoding='unic')
        wrapped = textwrap.wrap(text, width=39 if horizontal else 24)
        x, y = (34, 30)
        
        shadowcolor = (212, 169, 108)
        text = '\n'.join(wrapped)
        d.text((x-1, y-1), text, font=font, fill=shadowcolor)
        d.text((x+1, y-1), text, font=font, fill=shadowcolor)
        d.text((x-1, y+1), text, font=font, fill=shadowcolor)
        d.text((x+1, y+1), text, font=font, fill=shadowcolor)
        
        d.text((x, y), text, font=font, fill=(38, 31, 20))

        logo_font = ImageFont.truetype(f'cogs/packages/anarchy/assets/Coolvetica.otf', 30, encoding='unic')
        
        x, y = (imgdim[0] - 60, imgdim[1] - 70)
        d.text((x-1, y-1), '*', font=font, fill=shadowcolor)
        d.text((x+1, y-1), '*', font=font, fill=shadowcolor)
        d.text((x-1, y+1), '*', font=font, fill=shadowcolor)
        d.text((x+1, y+1), '*', font=font, fill=shadowcolor)
        d.text((x, y), '*', font=font, fill=(38, 31, 20))
        
        x, y = (imgdim[0] - 165, imgdim[1] - 70)
        d.text((x-1, y-1), 'Anarchy', font=logo_font, fill=shadowcolor)
        d.text((x+1, y-1), 'Anarchy', font=logo_font, fill=shadowcolor)
        d.text((x-1, y+1), 'Anarchy', font=logo_font, fill=shadowcolor)
        d.text((x+1, y+1), 'Anarchy', font=logo_font, fill=shadowcolor)
        d.text((x, y), 'Anarchy', font=logo_font, fill=(38, 31, 20))
        
        img = self.__add_corners(img, 30)
        return img
    
    # Commandes ================================================================
            
    @app_commands.command(name="start")
    @app_commands.guild_only()
    async def start_classic(self, interaction: discord.Interaction, rounds: app_commands.Range[int, 3, 21] = 7):
        """Lancer une partie de Anarchy avec les règles classique

        :param rounds: Nombre de tours de jeu, par défaut 7
        """
        channel = interaction.channel
        author = interaction.user
        if channel.type not in [discord.ChannelType.text, discord.ChannelType.public_thread, discord.ChannelType.private_thread]: #type: ignore
            return await interaction.response.send_message('Cette commande ne peut être utilisée que dans un salon de texte', ephemeral=True)
        if any([session.channel == channel for session in self.sessions]):
            return await interaction.response.send_message('Une partie est déjà en cours dans ce salon', ephemeral=True)

        session = ClassicGame(self, channel, rounds, author) #type: ignore
        self.sessions.append(session)
        
        # Sélection des packs de cartes
        if not await session.select_cardpacks(interaction):
            self.sessions.remove(session)
            return await interaction.followup.send("**Partie annulée ·** Aucun pack de cartes n'a été sélectionné", ephemeral=True)
        
        # Enregistrement des joueurs
        session.add_player(HumanPlayer(author))
        if not await session.register_players():
            self.sessions.remove(session)
            return await interaction.followup.send("**Partie annulée ·** Il n'y a pas assez de joueurs pour commencer la partie")
        
        # Lancement de la partie
        await session.start_game()
        self.sessions.remove(session)
        
    @app_commands.command(name="scoreboard")
    @app_commands.guild_only()
    async def show_scoreboard(self, interaction: discord.Interaction, top: app_commands.Range[int, 1, 30] = 10):
        """Affiche le scoreboard des joueurs d'Anarchy
        
        :param top: Nombre de joueurs à afficher, par défaut 10"""
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return await interaction.response.send_message('Cette commande ne peut être utilisée que dans un serveur', ephemeral=True)
        data = self.get_players_scores(guild) 
        if not data:
            return await interaction.response.send_message("**Erreur ·** Aucun joueur humain n'a encore remporté une partie à Anarchy", ephemeral=True)
        
        
        scoreboard = [(guild.get_member(user_id).name if guild.get_member(user_id) else user_id, score) for user_id, score in data][:top] #type: ignore
        em = discord.Embed(title="**Anarchy ·** Scoreboard", color=discord.Color.blurple())
        em.description = pretty.codeblock(tabulate(scoreboard, headers=['Joueur', 'Score']))
        em.set_footer(text=f"Top {top} • Chaque partie gagnée rapporte 1 point")
        await interaction.response.send_message(embed=em)
        
    @app_commands.command(name="customcard")
    async def custom_game_card(self, interaction: discord.Interaction, text: str, color: str, vertical: bool = True):
        """Créer une carte noire/blanche personnalisée

        :param text: Texte de la carte
        :param color: Couleur de la carte (noire/blanche/dorée)
        :param vertical: Si la carte doit être affichée verticalement
        """
        premium_role = None
        if isinstance(interaction.guild, discord.Guild):
            if interaction.guild.premium_subscription_count:
                premium_role = interaction.guild.premium_subscriber_role
    
        if color not in ['black', 'white', 'golden']:
            return await interaction.response.send_message("**Erreur ·** La couleur de la carte doit être `black` ou `white`", ephemeral=True)
        if '_' in text:
            text = text.replace('_', '________', 3)
        if len(text) > 200:
            return await interaction.response.send_message("**Erreur ·** Le texte de la carte ne peut pas dépasser 200 caractères", ephemeral=True)
        if color == 'black':
            bc = BlackCard(text)
            image = bc._generate_image(text, not vertical)
        elif color == 'white':
            image = self._generate_white_card(text, not vertical)
        else:
            if not isinstance(interaction.channel, discord.DMChannel):
                if not premium_role:
                    return await interaction.response.send_message("**Erreur ·** Cette commande n'est pas disponible sur ce serveur", ephemeral=True)
                elif premium_role not in interaction.user.roles:
                    return await interaction.response.send_message(f"**Erreur ·** Cette commande n'est disponible qu'aux membres possédant **@{premium_role.name}**", ephemeral=True)
            image = self._generate_gold_card(text, not vertical)
        
        with BytesIO() as f:
            image.save(f, format='PNG')
            f.seek(0)
            if color == 'golden' and premium_role and not isinstance(interaction.channel, discord.DMChannel):
                return await interaction.response.send_message(file=discord.File(f, 'card.png', description=text), content=f"*Non disponible en jeu, uniquement pour les membres **@{premium_role.name}***")
            await interaction.response.send_message(file=discord.File(f, 'card.png', description=text))
            
    @custom_game_card.autocomplete('color')
    async def autocomplete_callback(self, interaction: discord.Interaction, current: str):
        if isinstance(interaction.guild, discord.Guild) and interaction.guild.premium_subscription_count > 0:
            premium_role = interaction.guild.premium_subscriber_role
        else:
            premium_role = None
        choices = [app_commands.Choice(name='Noire', value='black'), app_commands.Choice(name='Blanche', value='white')]
        if premium_role or isinstance(interaction.channel, discord.DMChannel) :
            choices.append(app_commands.Choice(name='Dorée', value='golden'))
        return choices
            
            
async def setup(bot: commands.Bot):
    await bot.add_cog(Anarchy(bot))
