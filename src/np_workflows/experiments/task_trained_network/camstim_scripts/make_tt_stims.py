import pathlib

out_path = pathlib.Path(__file__).parent / 'stims'
movie_path = 'C:\\ProgramData\\StimulusFiles\\dev\\'

paths = {
    'old_stim.stim': movie_path+'old_movie\\ds_unwarped_15_test_movie_one.npy',
    'shuffle_reversed.stim': movie_path+'new_movies\\ds_unwarped_15_reverse.npy',
    'shuffle_reversed_1st.stim': movie_path+'new_movies\\ds_unwarped_15_reverse_first_half.npy',
    'shuffle_reversed_2nd.stim': movie_path+'new_movies\\ds_unwarped_15_reverse_second_half.npy',
}
paths.update({'densely_annotated_%02d.stim'%i: movie_path+'tt_stim_dense\\ds_unwarped_15_clip_%02d.npy'%i for i in range(19)})


if __name__ == '__main__':
    out_path.mkdir(exist_ok=True, parents=True)
    for file, path in paths.items():
        with open(out_path / file, 'w') as f:
            f.write('import os, shutil\n')
            f.write('import numpy as np\n')
            f.write('from camstim.misc import ImageStimNumpyuByte, checkDirs\n')
            f.write('moviesource = '+"r'"+path+"'"+'\n')
            f.write('stimulus = MovieStim(movie_path=moviesource,window=window,frame_length=2.0/60.0,size=(1920, 1200),start_time=0.0,stop_time=None,flip_v=True,runs=10,)')
