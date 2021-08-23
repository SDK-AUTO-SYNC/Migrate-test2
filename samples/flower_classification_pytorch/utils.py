# --------------------------------------------------------
# Swin Transformer
# Copyright (c) 2021 Microsoft
# Licensed under The MIT License [see LICENSE for details]
# Written by Ze Liu
# --------------------------------------------------------

import os
import torch
import torch.distributed as dist
from volcengine_ml_platform.tos import tos

try:
    # noinspection PyUnresolvedReferences
    from apex import amp
except ImportError:
    amp = None


def load_checkpoint(config, model, optimizer, lr_scheduler, logger):
    logger.info(
        f"==============> Resuming form {config.MODEL.RESUME}...................."
    )
    if config.MODEL.RESUME.startswith('https'):
        checkpoint = torch.hub.load_state_dict_from_url(config.MODEL.RESUME,
                                                        map_location='cpu',
                                                        check_hash=True)
    elif config.MODEL.LOAD_PRETRAINED:
        checkpoint = torch.load('./swin_tiny_patch4_window7_224.pth',
                                map_location='cpu')
    elif config.MODEL.LOAD_CHECKPOINT:
        checkpoint = torch.load('./ckpt.pth', map_location='cpu')

    if config.MODEL.LOAD_PRETRAINED:
        model_dict = model.state_dict()
        checkpoint['model'] = {
            k: v for k, v in checkpoint['model'].items() if k in model_dict and
            'head.weight' not in k and 'head.bias' not in k
        }
    msg = model.load_state_dict(checkpoint['model'], strict=False)
    logger.info(msg)
    max_accuracy = 0.0
    if not config.EVAL_MODE and 'optimizer' in checkpoint and 'lr_scheduler' in checkpoint and 'epoch' in checkpoint:
        optimizer.load_state_dict(checkpoint['optimizer'])
        lr_scheduler.load_state_dict(checkpoint['lr_scheduler'])
        config.defrost()
        config.TRAIN.START_EPOCH = checkpoint['epoch'] + 1
        config.freeze()
        if 'amp' in checkpoint and config.AMP_OPT_LEVEL != "O0" and checkpoint[
                'config'].AMP_OPT_LEVEL != "O0":
            amp.load_state_dict(checkpoint['amp'])
        logger.info(
            f"=> loaded successfully '{config.MODEL.RESUME}' (epoch {checkpoint['epoch']})"
        )
        if 'max_accuracy' in checkpoint:
            max_accuracy = checkpoint['max_accuracy']

    del checkpoint

    if config.DEVICE == 'cuda':
        torch.cuda.empty_cache()
    return max_accuracy


def save_checkpoint(config, epoch, model, max_accuracy, optimizer, lr_scheduler,
                    logger, bucket):
    save_state = {
        'model': model.state_dict(),
        'optimizer': optimizer.state_dict(),
        'lr_scheduler': lr_scheduler.state_dict(),
        'max_accuracy': max_accuracy,
        'epoch': epoch,
        'config': config
    }
    if config.AMP_OPT_LEVEL != "O0":
        save_state['amp'] = amp.state_dict()

    save_path = os.path.join(config.OUTPUT, f'ckpt.pth')
    logger.info(f"{save_path} saving......")
    torch.save(save_state, save_path)
    logger.info(f"{save_path} saved !!!")
    client = tos.TOSClient()

    client.upload_file('./output/swin_tiny_patch4_window7_224/default/ckpt.pth',
                       bucket=bucket,
                       key='flower-classification/checkpoints/pytorch_ckpt.pth')
    logger.info(f"{save_path} uploaded !!!")


def get_grad_norm(parameters, norm_type=2):
    if isinstance(parameters, torch.Tensor):
        parameters = [parameters]
    parameters = list(filter(lambda p: p.grad is not None, parameters))
    norm_type = float(norm_type)
    total_norm = 0
    for p in parameters:
        param_norm = p.grad.data.norm(norm_type)
        total_norm += param_norm.item()**norm_type
    total_norm = total_norm**(1. / norm_type)
    return total_norm


def auto_resume_helper(output_dir):
    checkpoints = os.listdir(output_dir)
    checkpoints = [ckpt for ckpt in checkpoints if ckpt.endswith('pth')]
    print(f"All checkpoints founded in {output_dir}: {checkpoints}")
    if len(checkpoints) > 0:
        latest_checkpoint = max(
            [os.path.join(output_dir, d) for d in checkpoints],
            key=os.path.getmtime)
        print(f"The latest checkpoint founded: {latest_checkpoint}")
        resume_file = latest_checkpoint
    else:
        resume_file = None
    return resume_file


def reduce_tensor(tensor):
    rt = tensor.clone()
    dist.all_reduce(rt, op=dist.ReduceOp.SUM)
    rt /= dist.get_world_size()
    return rt