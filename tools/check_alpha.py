import numpy as np
from PIL import Image

pet = np.array(Image.open(r"assets\layers\pet.png"))
body = np.array(Image.open(r"assets\layers\body.png"))
head = np.array(Image.open(r"assets\layers\head.png"))
box = (slice(300, 370), slice(170, 260))
print("pet alpha in chest box :", round(float((pet[box][:, :, 3] == 0).mean()), 3), "transparent ratio")
print("body alpha in chest box:", round(float((body[box][:, :, 3] == 0).mean()), 3))
print("head alpha in chest box:", round(float((head[box][:, :, 3] == 0).mean()), 3))
print("vest pixel (340,210):", pet[340, 210, :3], " (330,240):", pet[330, 240, :3])
