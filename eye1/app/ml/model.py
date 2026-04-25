import torch
import torch.nn as nn
import timm

NUM_CLASSES = 4  # no-damage, minor, major, destroyed


class DamageClassifier(nn.Module):
    """
    EfficientNet-B4 fine-tuned for building damage classification.
    Takes 6-channel input (pre + post disaster image stacked).
    """
    def __init__(self, num_classes=NUM_CLASSES, pretrained=True, dropout=0.3):
        super().__init__()

        self.backbone = timm.create_model(
            'efficientnet_b4',
            pretrained=pretrained,
            num_classes=0,  # remove head
            in_chans=6      # 6-channel input (pre+post)
        )

        feature_dim = self.backbone.num_features  # 1792 for B4

        self.classifier = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(feature_dim, 512),
            nn.ReLU(),
            nn.Dropout(dropout / 2),
            nn.Linear(512, num_classes)
        )

    def forward(self, x):
        features = self.backbone(x)
        return self.classifier(features)


def get_model(pretrained=True):
    model = DamageClassifier(pretrained=pretrained)
    total = sum(p.numel() for p in model.parameters())
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Total params: {total:,} | Trainable: {trainable:,}")
    return model
