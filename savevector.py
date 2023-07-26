#!/usr/bin/env python
# coding = utf-8

import logging
import os
import random
import sys
import pickle
from dataclasses import dataclass, field
from typing import Optional
from tqdm import tqdm

import evaluate
import numpy as np
import torch
import transformers
from transformers import (AutoConfig, AutoModelForTokenClassification,
                          AutoTokenizer, HfArgumentParser, Trainer,
                          TrainingArguments, set_seed)
from transformers.trainer_utils import get_last_checkpoint, is_main_process
from transformers.utils import check_min_version
from transformers import T5Tokenizer

from core.common.utils import random_split, visualize_text_layout_task, visualize_text_task, visualize_layout_task, img_trans_torchvision, get_visual_bbox
from core.datasets import RvlCdipDataset
from core.models import (UdopConfig, UdopTokenizer,
                         UdopUnimodelForConditionalGeneration)
from core.trainers import DataCollator
from PIL import Image

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

####### 우리는 udop unimodel로 사용 ###
MODEL_CLASSES = {
    #'UdopDual': (UdopConfig, UdopDualForConditionalGeneration, UdopTokenizer),
    'UdopUnimodel': (UdopConfig, UdopUnimodelForConditionalGeneration, UdopTokenizer),
}
##################################

# Will error if the minimal version of Transformers is not installed. Remove at your own risks.
check_min_version("4.6.0")

logger = logging.getLogger(__name__)


@dataclass
class DataTrainingArguments:
    """
    Arguments pertaining to what data we are going to input our model for training and eval.
    """
    task_name: Optional[str] = field(default="ner", metadata={"help": "The name of the task (ner, pos...)."})
    data_dir: Optional[str] = field( #어짜피 안씀1
        default=None, metadata={"help": "local dataset stored location"},
    )
    dataset_name: Optional[str] = field( #어짜피 안씀2
        default=None, metadata={"help": "The name of the dataset to use (via the datasets library)."}
    )
    dataset_config_name: Optional[str] = field( #어짜피 안씀3
        default=None, metadata={"help": "The configuration name of the dataset to use (via the datasets library)."}
    )
    train_file: Optional[str] = field( #어짜피 안씀4
        default=None, metadata={"help": "The input training data file (a csv or JSON file)."}
    )
    validation_file: Optional[str] = field( #어짜피 안씀5
        default=None,
        metadata={"help": "An optional input evaluation data file to evaluate on (a csv or JSON file)."},
    )
    test_file: Optional[str] = field( #어짜피 안씀6
        default=None,
        metadata={"help": "An optional input test data file to predict on (a csv or JSON file)."},
    )
    overwrite_cache: bool = field(
        default=True, metadata={"help": "Overwrite the cached training and evaluation sets"}
    )
    preprocessing_num_workers: Optional[int] = field( ######### 컴터 사양 따라 조절, default 8
        default=2,
        metadata={"help": "The number of processes to use for the preprocessing."},
    )
    pad_to_max_length: bool = field(
        default=False,
        metadata={
            "help": "Whether to pad all samples to model maximum sentence length. "
            "If False, will pad the samples dynamically when batching to the maximum length in the batch. More "
            "efficient on GPU but very bad for TPU."
        },
    )
    max_train_samples: Optional[int] = field(
        default=None,
        metadata={
            "help": "For debugging purposes or quicker training, truncate the number of training examples to this "
            "value if set."
        },
    )
    max_val_samples: Optional[int] = field(
        default=None,
        metadata={
            "help": "For debugging purposes or quicker training, truncate the number of validation examples to this "
            "value if set."
        },
    )
    max_test_samples: Optional[int] = field(
        default=None,
        metadata={
            "help": "For debugging purposes or quicker training, truncate the number of test examples to this "
            "value if set."
        },
    )
    image_size: Optional[int] = field(
    default=224,
    metadata={
        "help": "image size"
        "value if set."
    },
    )
    max_seq_length: int = field(
        default=1024,
        metadata={
            'help':
            'The maximum total input sequence length after tokenization. Sequences longer '
            'than this will be truncated, sequences shorter will be padded.'
        },
    )   
    max_seq_length_decoder: int = field(
        default=16,
        metadata={
            'help':
            'The maximum total input sequence length after tokenization. Sequences longer '
            'than this will be truncated, sequences shorter will be padded.'
        },
    )    


@dataclass
class ModelArguments:
    """
    Arguments pertaining to which model/config/tokenizer we are going to fine-tune from.
    """
    model_name_or_path: str = field( 
        default='t5-large', 
        metadata={"help": "Path to pretrained model or model identifier from huggingface.co/models"}
    )
    model_type: str = field( #우리는 UdopUnimodel 가정.
        default='UdopUnimodel', metadata={'help': 'Model type selected in the list.'})
    config_name: Optional[str] = field(
        default=None, metadata={"help": "Pretrained config name or path if not the same as model_name"}
    )
    tokenizer_name: Optional[str] = field( #tokenizer 경로. custom사용 가정
        default="tokenizer_finetuned_ket5_by_xml_data", 
        metadata={"help": "Pretrained tokenizer name or path if not the same as model_name"}
    )
    cache_dir: Optional[str] = field( #pretrained model 저장 장소
        default=None,
        metadata={"help": "Where do you want to store the pretrained models downloaded from huggingface.co"},
    )
    model_revision: str = field( #기존 version 사용할 경우 이름..? 저장인가
        default="main",
        metadata={"help": "The specific model version to use (can be a branch name, tag name or commit id)."},
    )
    use_auth_token: bool = field( #model token 추가할 때 사용하는 변수.
        default=False,
        metadata={
            "help": "Will use the token generated when running `transformers-cli login` (necessary to use this script "
            "with private models)."
        },
    )    
    attention_type: str = field( #이건 안바꾸는게 좋을 듯.
        default="original_full",
        metadata={"help": "Attention type: BigBird configuruation only. Choices: block_sparse (default) or original_full"},
    )


def main():
    # See all possible arguments in layoutlmft/transformers/training_args.py
    # or by passing the --help flag to this script.
    # We now keep distinct sets of args, for a cleaner separation of concerns.
    
    parser = HfArgumentParser((ModelArguments, DataTrainingArguments, TrainingArguments))
    if len(sys.argv) == 2 and sys.argv[1].endswith(".json"):
        # If we pass only one argument to the script and it's the path to a json file,
        # let's parse it to get our arguments.
        model_args, data_args, training_args = parser.parse_json_file(json_file=os.path.abspath(sys.argv[1]))
    else:
        model_args, data_args, training_args = parser.parse_args_into_dataclasses()


    training_args.logging_dir = os.path.join(training_args.output_dir, 'runs')
    if model_args.cache_dir is None:
        model_args.cache_dir = os.path.join(training_args.output_dir, 'cache')
    os.makedirs(model_args.cache_dir, exist_ok=True)

    # Detecting last checkpoint.
    last_checkpoint = None
    if os.path.isdir(training_args.output_dir) and training_args.do_train and not training_args.overwrite_output_dir:
        last_checkpoint = get_last_checkpoint(training_args.output_dir)
        if last_checkpoint is None and len(os.listdir(training_args.output_dir)) > 0:
            raise ValueError(
                f"Output directory ({training_args.output_dir}) already exists and is not empty. "
                "Use --overwrite_output_dir to overcome."
            )
        elif last_checkpoint is not None:
            logger.info(
                f"Checkpoint detected, resuming training at {last_checkpoint}. To avoid this behavior, change "
                "the `--output_dir` or add `--overwrite_output_dir` to train from scratch."
            )

    # Setup logging
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(name)s -   %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logger.setLevel(logging.INFO if is_main_process(training_args.local_rank) else logging.WARN)

    # Log on each process the small summary:
    logger.warning(
        f"Process rank: {training_args.local_rank}, device: {training_args.device}, n_gpu: {training_args.n_gpu} "
        + f"distributed training: {bool(training_args.local_rank != -1)}, 16-bits training: {training_args.fp16}"
    )
    # Set the verbosity to info of the Transformers logger (on main process only):
    if is_main_process(training_args.local_rank):
        transformers.utils.logging.set_verbosity_info()
        transformers.utils.logging.enable_default_handler()
        transformers.utils.logging.enable_explicit_format()
    
    logger.info(f"Training/evaluation parameters {training_args}")
    logger.info(f"Data arguments: {data_args}")
    logger.info(f"Model arguments: {model_args}")

    # Set seed before initializing model.
    set_seed(training_args.seed)

 
    #if 'local' in model_args.model_name_or_path:
    if model_args.model_type in MODEL_CLASSES:
        config_type, model_type, tokenizer_type = MODEL_CLASSES[model_args.model_type]
    else:
        config_type, model_type, tokenizer_type = AutoConfig, AutoModelForTokenClassification, AutoTokenizer

    # Load pretrained model and tokenizer
    #
    # Distributed training:
    # The .from_pretrained methods guarantee that only one local process can concurrently
    # download model & vocab.
    num_labels = 2

    config = config_type.from_pretrained(
        model_args.config_name if model_args.config_name else model_args.model_name_or_path,
        num_labels=num_labels,
        finetuning_task=data_args.task_name,
        cache_dir=model_args.cache_dir,
        revision=model_args.model_revision,
        use_auth_token=True if model_args.use_auth_token else None,
        attention_type=model_args.attention_type if model_args.attention_type else None,
    )
    
    ########################### when using custom tokenizer ################################

    #tokenizer = AutoTokenizer.from_pretrained("tokenizer_finetuned_ket5_by_xml_data")
    '''
    tokenizer = UdopTokenizer.from_pretrained(
        model_args.tokenizer_name if model_args.tokenizer_name else model_args.model_name_or_path,
        cache_dir=model_args.cache_dir,
        use_fast=True,
        revision=model_args.model_revision,
        use_auth_token=True if model_args.use_auth_token else None,
    )
    '''
    tokenizer = AutoTokenizer.from_pretrained('ket5-finetuned')
    #######################################################################################
    
    model = model_type.from_pretrained("checkpoint-180000")
    '''
    with open('./modelconfig.json', 'r') as jsonfile:
        cf = json.load(jsonfile)
    ptcf = PretrainedConfig(**cf)
    print(ptcf)
    model = UdopUnimodelForConditionalGeneration(ptcf)

    print(torch.cuda.get_device_name(0))
    '''
    ################################################### revised start ############################################
    json_path = '../data/json_data'
    image_path = '../data/image'
    
    indexmap = None
    with open('./no_outside_lst_new.pickle','rb') as f:
        indexmap = pickle.load(f)
        f.close()
   # Get datasets
    all_dataset = (RvlCdipDataset(json_path = json_path,
                                    image_path = image_path,
                                    index_map = indexmap,                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     data_args=data_args, 
                                    tokenizer=tokenizer, 
                                    mode='all')
                     if training_args.do_train else None)                 

    ################################################### revised end ############################################

    # Data collator
    padding = "max_length" if data_args.pad_to_max_length else False
    data_collator = DataCollator(
        tokenizer=tokenizer,
        padding=padding,
        max_length=data_args.max_seq_length,
        max_length_decoder=data_args.max_seq_length_decoder,
    )

    encode_pickle = []

    for i in tqdm(range(len(all_dataset))):
        data = all_dataset[i]
        data['input_ids'] = torch.unsqueeze(data['input_ids'],dim=0)
        data['seg_data'] = torch.unsqueeze(data['seg_data'],dim=0)
        output = model.forward(
                    input_ids = data['input_ids'],
                    seg_data=data['seg_data'],
                    use_cache=True,
                    decoder_start_token_id=tokenizer.pad_token_id,
                    num_beams=1,
                    max_length=512,
                )
        encoded_vector = output['last_hidden_state'][0][0].detach().numpy()
        del output
        del data
        encode_pickle.append(encoded_vector)
    
    with open('encoded_vectors_noimage.pickle','wb') as f:
        pickle.dump(encode_pickle, f)
        f.close()

        

def _mp_fn(index):
    # For xla_spawn (TPUs)
    main()


if __name__ == "__main__":
    main()