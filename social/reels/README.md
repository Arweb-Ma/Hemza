# Reels Instagram (1080×1920, 30 fps)

`reels.json` décrit chaque reel (scènes : `hook`, `deal`, `versus`, `tips`, `end`) avec des prix tirés de Supabase.
`python3 render.py` écrit `out/<id>.mp4` (piste audio muette : ajouter un son tendance dans Instagram).
`python3 render.py --frames` dessine seulement des images de contrôle dans `check/`.
Besoins : Python + Pillow, ffmpeg, police Inter installée ; Fraunces (OFL) est fournie dans `fonts/`.
Toujours vérifier l'historique des prix (`price_history`) avant de citer un prix : certaines fiches alternent entre deux variantes.
