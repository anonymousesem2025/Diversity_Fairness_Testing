import random
import time
from sklearn.tree import DecisionTreeClassifier
import csv
from utils.PathSearcher import PathSearcher, IntervalPool
import logging
import itertools
import os
from config import config_census
from config import config_german
from config import config_bank


class AFT:
    def __init__(self, black_box_model, protected_list, no_train_data_sample, show_logging=False):
        self.black_box_model = black_box_model
        self.train_data = list()
        self.disc_data = list()
        self.test_data = list()
        self.protected_list = [self.black_box_model.feature_list[i] for i in protected_list]
        self.protected_list_no = protected_list
        self.no_train_data_sample = no_train_data_sample
        self.no_test = 0
        self.no_disc = 0
        self.real_time_consumed = 0
        self.cpu_time_consumed = 0
        self.protected_value_comb = self.generate_protected_value_combination()
        self.no_prot = len(protected_list)
        if show_logging:
            logging.basicConfig(format="", level=logging.INFO)
        else:
            logging.basicConfig(level=logging.CRITICAL + 1)

    def generate_protected_value_combination(self):
        res = list()
        for index_protected in self.protected_list_no:
            MinMax = self.black_box_model.data_range[index_protected]
            res.append(list(range(MinMax[0], MinMax[1] + 1)))
        return list(itertools.product(*res))

    def create_train_data(self, num):
        self.train_data = list()
        black_model = self.black_box_model
        data_range = black_model.data_range
        for _ in range(num):
            temp = list()
            for i in range(black_model.no_attr):
                temp.append(random.randint(data_range[i][0], data_range[i][1]))
            temp.append(int(black_model.predict([temp])))
            self.train_data.append(temp)

    def train_approximate_DT(self, max_leaf_nodes):
        X = [item[:-1] for item in self.train_data]
        Y = [item[-1] for item in self.train_data]
        clf = DecisionTreeClassifier(criterion="entropy", splitter="best", max_depth=None, min_samples_split=2,
                                       min_samples_leaf=1, min_weight_fraction_leaf=0.0, max_features=None,
                                       random_state=None, max_leaf_nodes=max_leaf_nodes)
        return clf.fit(X, Y)
    
    def test(self, runtime=None, max_leaf_nodes=1000, max_test_data=None, label=("res",0),
             dt_search_mode="random+flip", check_type="themis", MaxTry=10000, MaxDiscPathPair=100, max_train_data_each_path=10,
             max_sample_each_path=100):
        start_real_time = time.time()
        start_cpu_time = time.process_time()
        restart_flag = True
        self.no_test = 0
        no_new_train_count = 0
        loop = 0
        IntervalP = IntervalPool()

        # Main loop of AFT (Algorithm 2 in paper)
        logging.info(f"Starting fairness test -- {label[0]}")
        
        method, model, dataset, protected_attr, runtime_str = label[0].split("-")
        
        while True:
            loop += 1
            if (runtime is not None) and (time.process_time() - start_cpu_time >= runtime):
                break
            if (max_test_data is not None) and (self.no_test >= max_test_data):
                break
            if dataset == "Adult" and self.no_disc >= 32561:
                break
            if dataset == "Bank" and self.no_disc >= 45211:
                break
            if dataset == "Credit" and self.no_disc >= 1000:
                break

            # Generate training data from input space of CuT at random.
            if restart_flag:
                self.create_train_data(self.no_train_data_sample)
                restart_flag = False

            # (Re-)train an approximate decision tree model with training data, as an approximation of CuT.
            DT = self.train_approximate_DT(max_leaf_nodes=max_leaf_nodes)

            # Generate test cases and then identify discriminatory instances through path samping and random search
            # (see the function sampler.sample()).
            sampler = PathSearcher(DT=DT, CuT=self.black_box_model, data_range=self.black_box_model.data_range,
                                   protected_value_comb=self.protected_value_comb, protected_list_no=self.protected_list_no, IntervalP=IntervalP)
            satFlag = sampler.sample(dt_search_mode=dt_search_mode, check_type=check_type, MaxTry=MaxTry, MaxDiscPathPair=MaxDiscPathPair,
                                     max_train_data_each_path=max_train_data_each_path, max_sample_each_path=max_sample_each_path)

            if satFlag:
                # If at least one test case is found,
                # update test cases and discriminatory instances
                test_data = sampler.get_test_data()
                self.no_test += len(test_data) // 2
                self.test_data += test_data
                new_disc_data = sampler.get_disc_data()
                self.no_disc += len(new_disc_data) // 2
                if len(new_disc_data) == 0:
                    # If no one discriminatory instance is found in this iteration, then restart
                    restart_flag = True
                    logging.info(f"Restarting due to not finding any discriminatory data in this loop")
                    logging.info(f"Loop {loop}: #Disc={self.no_disc}, #Test={self.no_test}")
                    continue
                else:                 
                    filtered_disc_data = [row for row in new_disc_data if row[-1] == 0] # modified
                    non_label_filtered_disc_data = [row[:-1] for row in filtered_disc_data] # modified
                    self.disc_data += non_label_filtered_disc_data 
                    #self.disc_data += new_disc_data

                # Update the training data
                new_train_data = sampler.get_train_data()
                self.train_data += new_train_data
                if len(new_train_data) == 0:
                    no_new_train_count += 1
                    if no_new_train_count >= 5:
                        restart_flag = True
                        no_new_train_count = 0
                else:
                    no_new_train_count = 0
            else:
                # If no one test case could be found from the decision tree, then restart the loop
                restart_flag = True
                #logging.info(f"Restarting due to not finding any test cases in this loop")
            #logging.info(f"Loop {loop}: #Disc={self.no_disc}, #Test={self.no_test}")

        self.real_time_consumed = time.time() - start_real_time
        self.cpu_time_consumed = time.process_time() - start_cpu_time
        
        # select random disc data
        if dataset == "Adult" or dataset == "Bank":
            if len(self.disc_data) >= 1000:
                self.disc_data = random.sample(self.disc_data, 1000)
        elif dataset == "Credit":
            if len(self.disc_data) >= 50:
                self.disc_data = random.sample(self.disc_data, 50)
       
        # save the results of detected discriminatory instances and generated test cases
        
        if model == "SVM":
            model = "SVM"
        elif model == "MLP":
            model ="MLPC"
        elif model == "RanForest":
            model = "RF"
        dataset_and_attr = f"{dataset}-{protected_attr}" 
        
        current_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(current_dir, "..", "distance_results", method.upper(), dataset_and_attr, model)
        output_dir = os.path.abspath(output_dir)

   
        output_dir = f"../distance_results/{method.upper()}/{dataset_and_attr}/{model}"
        if not os.path.exists(output_dir):
            print(f"{output_dir}path")
            raise FileNotFoundError
        disc_file = os.path.join(output_dir, f"{label[0]}-{label[1]}.csv")
        
        logging.info(f"The fairness test is completed")
        #logging.info(f"Saving the generated test cases to TestData/{label[0]}-{label[1]}.csv")
        #with open(f'TestData/{label[0]}-{label[1]}.csv', 'w', newline='') as csvfile:
        #    csvwriter = csv.writer(csvfile)
        #    csvwriter.writerows(self.test_data)
        
        logging.info(f"Saving the detected discriminatory instances to {output_dir}/{label[0]}-{label[1]}.csv")
        with open(disc_file, 'w', newline='') as csvfile: 
            csvwriter = csv.writer(csvfile)
            csvwriter.writerows(self.disc_data)
        logging.info(f"Finished")
        
        # check distance      
        def L1_distance(disc1,disc2):
            distance = 0
            for i in range(len(disc1)):
                #print(distance)
                difference = abs(disc1[i]-disc2[i])
                min_val, max_val = self.black_box_model.data_range[i]
                if dataset=="Adult":
                    distance += float(difference)/(max_val - min_val)
                elif dataset=="Bank":
                    distance += float(difference)/(max_val - min_val)
                elif dataset=="Credit":
                    distance += float(difference)/(max_val - min_val)
            return distance
        
        pairwisedistance = 0
        count=0
        disc_num = len(self.disc_data)
        for i in range(0,disc_num-1):
            for j in range(i+1,disc_num):
                pairwisedistance += L1_distance(self.disc_data[i],self.disc_data[j])
                count += 1
        pairwisedistance = float(pairwisedistance) / count
        
        
        # Save pairwise distance
        pairwise_file = os.path.join(output_dir, "pairwise_distance.txt")
        with open(pairwise_file, 'a') as f: 
            f.write(f"{pairwisedistance} ({label[0]}-{label[1]})\n")
        
        print(f"Pairwise distance ({pairwisedistance:.6f}) saved to: {pairwise_file}")

        
