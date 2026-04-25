import os
import json
import numpy as np
from PIL import Image
from shapely.geometry import shape
import torch
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms

DAMAGE_LABELS = {
    'no-damage': 0,
    'minor-damage': 1,
    'major-damage': 2,
    'destroyed': 3,
    'un-classified': 0  # treat as no-damage
}

class XBDDataset(Dataset):
    """
    Loads pre/post disaster image pairs from xBD challenge format.
    Crops individual buildings and returns (crop, label) pairs.
    """
    def __init__(self, data_dir, split='train', transform=None, chip_size=128):
        self.data_dir = data_dir
        self.split = split
        self.transform = transform
        self.chip_size = chip_size
        self.samples = []  # list of (pre_path, post_path, bbox, label)
        self._build_index()

    def _build_index(self):
        images_dir = os.path.join(self.data_dir, self.split, 'images')
        labels_dir = os.path.join(self.data_dir, self.split, 'labels')

        if not os.path.exists(images_dir):
            raise FileNotFoundError(f"Images dir not found: {images_dir}")

        post_images = [
            f for f in os.listdir(images_dir)
            if 'post_disaster' in f and f.endswith('.png')
        ]

        print(f"Found {len(post_images)} post-disaster images in {self.split}")

        for post_fname in post_images:
            pre_fname = post_fname.replace('post_disaster', 'pre_disaster')
            label_fname = post_fname.replace('.png', '.json')

            post_path = os.path.join(images_dir, post_fname)
            pre_path = os.path.join(images_dir, pre_fname)
            label_path = os.path.join(labels_dir, label_fname)

            if not os.path.exists(pre_path) or not os.path.exists(label_path):
                continue

            with open(label_path) as f:
                label_data = json.load(f)

            features = label_data.get('features', {}).get('xy', [])
            for feature in features:
                props = feature.get('properties', {})
                subtype = props.get('subtype', 'no-damage')
                label = DAMAGE_LABELS.get(subtype, 0)

                try:
                    geom = shape(feature['wkt'] if 'wkt' in feature
                                 else feature.get('geometry', {}))
                    bounds = geom.bounds  # (minx, miny, maxx, maxy)
                    if bounds[2] - bounds[0] < 4 or bounds[3] - bounds[1] < 4:
                        continue  # skip tiny polygons
                    self.samples.append((pre_path, post_path, bounds, label))
                except Exception:
                    continue

        print(f"Total building chips: {len(self.samples)}")
        self._print_class_dist()

    def _print_class_dist(self):
        from collections import Counter
        counts = Counter(s[3] for s in self.samples)
        names = {v: k for k, v in DAMAGE_LABELS.items()}
        for idx, count in sorted(counts.items()):
            print(f"  {names.get(idx, idx)}: {count}")

    def _crop(self, img, bounds):
        """Crop image to building bounding box with padding."""
        w, h = img.size
        minx, miny, maxx, maxy = bounds
        pad = 10
        x1 = max(0, int(minx) - pad)
        y1 = max(0, int(miny) - pad)
        x2 = min(w, int(maxx) + pad)
        y2 = min(h, int(maxy) + pad)
        return img.crop((x1, y1, x2, y2))

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        pre_path, post_path, bounds, label = self.samples[idx]

        pre_img = Image.open(pre_path).convert('RGB')
        post_img = Image.open(post_path).convert('RGB')

        pre_crop = self._crop(pre_img, bounds).resize(
            (self.chip_size, self.chip_size), Image.BILINEAR)
        post_crop = self._crop(post_img, bounds).resize(
            (self.chip_size, self.chip_size), Image.BILINEAR)

        # Stack pre/post as 6-channel input
        pre_arr = np.array(pre_crop, dtype=np.float32) / 255.0
        post_arr = np.array(post_crop, dtype=np.float32) / 255.0
        combined = np.concatenate([pre_arr, post_arr], axis=2)  # (H, W, 6)
        combined = torch.tensor(combined).permute(2, 0, 1)  # (6, H, W)

        if self.transform:
            combined = self.transform(combined)

        return combined, label


def get_dataloaders(data_dir, batch_size=32, chip_size=128, num_workers=4):
    train_ds = XBDDataset(data_dir, split='train', chip_size=chip_size)
    test_ds = XBDDataset(data_dir, split='test', chip_size=chip_size)

    train_loader = DataLoader(
        train_ds, batch_size=batch_size, shuffle=True,
        num_workers=num_workers, pin_memory=True
    )
    test_loader = DataLoader(
        test_ds, batch_size=batch_size, shuffle=False,
        num_workers=num_workers, pin_memory=True
    )

    return train_loader, test_loader
