# Reels Instagram (1080×1920, 30 fps)

`reels.json` décrit chaque reel avec des prix tirés de Supabase.
Scènes : `hook`, `deal`, `versus`, `tips`, `end` et, depuis la v2, `hook2` (titre + éventail de photos),
`product` (photo produit + prix + mini-historique des prix) et `split` (« sur ces N promos, X sont vraies »).
Une barre de progression par scène (style stories) est dessinée en haut.

## Photos produits
Chaque scène `product` indique `image` (ex. `img/lacoste-4771.jpg`) et `image_url` (colonne `products.image_url`).
- `py fetch_images.py <id-du-reel>` télécharge les photos dans `img/` (à lancer depuis le PC : certains sites bloquent les serveurs).
- Ou dépose toi-même une photo au même chemin `img/…` (capture d'écran recadrée, etc.).
- Photo absente : la scène affiche à la place un grand graphique de l'historique des prix. Le reel reste donc publiable sans photos.

## Rendu
`py render.py <id-du-reel>` écrit `out/<id>.mp4` (piste audio muette : ajouter un son tendance dans Instagram).
`py render.py <id> --frames` dessine seulement des images de contrôle dans `check/`.
Besoins : Python + Pillow et ffmpeg (sinon `pip install imageio-ffmpeg`, utilisé automatiquement).
Polices fournies dans `fonts/` : Fraunces et Inter (licence SIL OFL 1.1).

Toujours vérifier l'historique des prix (`price_history`) avant de citer un prix : certaines fiches alternent entre deux variantes.
